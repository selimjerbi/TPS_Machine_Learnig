from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from typing import Dict, Any, List

import pandas as pd

app = FastAPI()

store = None
model = None


class PredictIn(BaseModel):
    user_id: str


# Les 14 features attendues par ton modèle (signature MLflow)
FEATURES: List[str] = [
    "subs_profile_fv:months_active",
    "subs_profile_fv:monthly_fee",
    "subs_profile_fv:paperless_billing",
    "subs_profile_fv:plan_stream_tv",
    "subs_profile_fv:plan_stream_movies",
    "subs_profile_fv:net_service",
    "usage_agg_30d_fv:watch_hours_30d",
    "usage_agg_30d_fv:avg_session_mins_7d",
    "usage_agg_30d_fv:unique_devices_30d",
    "usage_agg_30d_fv:skips_7d",
    "usage_agg_30d_fv:rebuffer_events_7d",
    "payments_agg_90d_fv:failed_payments_90d",
    "support_agg_90d_fv:support_tickets_90d",
    "support_agg_90d_fv:ticket_avg_resolution_hrs_90d",
]

# Colonnes attendues par le modèle (sans préfixe FV)
MODEL_INPUT_COLUMNS = [
    "months_active",
    "monthly_fee",
    "paperless_billing",
    "plan_stream_tv",
    "plan_stream_movies",
    "net_service",
    "watch_hours_30d",
    "avg_session_mins_7d",
    "unique_devices_30d",
    "skips_7d",
    "rebuffer_events_7d",
    "failed_payments_90d",
    "support_tickets_90d",
    "ticket_avg_resolution_hrs_90d",
]


def init():
    """Initialise Feast + modèle MLflow (lazy loading)."""
    global store, model

    if store is None:
        from feast import FeatureStore
        store = FeatureStore(repo_path="/repo")

    if model is None:
        import mlflow
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
        model_name = os.getenv("MLFLOW_MODEL_NAME", "streamflow_churn")
        # Modèle en Production (stage)
        model = mlflow.pyfunc.load_model(f"models:/{model_name}/Production")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict")
def predict(inp: PredictIn):
    # 1) init Feast + modèle
    try:
        init()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Model or feature store not initialized: {e}",
        )

    # 2) Récupération features online
    try:
        feature_dict = store.get_online_features(
            features=FEATURES,
            entity_rows=[{"user_id": inp.user_id}],
        ).to_dict()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "feast_online_lookup_failed", "message": str(e)},
        )

    # 3) Mise en forme {feature_name: scalar}
    simple: Dict[str, Any] = {k.split(":")[-1]: v[0] for k, v in feature_dict.items()}

    # 4) Sanity checks : missing / user inconnu / fenêtre materialize
    missing = [k for k, v in simple.items() if v is None]
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_features",
                "user_id": inp.user_id,
                "missing": missing,
                "features": simple,
                "hint": "Vérifie que l'utilisateur existe et que feast materialize couvre la bonne fenêtre.",
            },
        )

    # 5) Construire X exactement comme attendu (PAS de user_id)
    # On force l'ordre des colonnes pour coller au schéma
    X = pd.DataFrame([[simple[c] for c in MODEL_INPUT_COLUMNS]], columns=MODEL_INPUT_COLUMNS)

    # 6) Prédiction
    try:
        pred = model.predict(X)
        # pred peut être array/list ; on prend le 1er
        pred0 = pred[0] if hasattr(pred, "__len__") else pred
        # conversion robuste
        pred_int = int(pred0)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "model_predict_failed",
                "message": str(e),
                "input_row": simple,
            },
        )

    return {
        "user_id": inp.user_id,
        "prediction": pred_int,
        "features": simple,
    }
