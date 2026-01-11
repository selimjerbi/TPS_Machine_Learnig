import os
import time

import pandas as pd
from sqlalchemy import create_engine

from feast import FeatureStore

from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, f1_score, accuracy_score
from sklearn.ensemble import RandomForestClassifier
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient
from mlflow.exceptions import MlflowException

from prefect import flow, task

# --- Local helper (unit-tested)
from compare_utils import should_promote


MODEL_NAME = "streamflow_churn"
FEAST_REPO = os.getenv("FEAST_REPO", "/repo")

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_EXPERIMENT = os.getenv("MLFLOW_EXPERIMENT", "streamflow")

# Features Feast (identique au TP)
FEATURES = [
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


def get_sql_engine():
    uri = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER','streamflow')}:"
        f"{os.getenv('POSTGRES_PASSWORD','streamflow')}@"
        f"{os.getenv('POSTGRES_HOST','postgres')}:5432/"
        f"{os.getenv('POSTGRES_DB','streamflow')}"
    )
    return create_engine(uri)


def fetch_entity_df(engine, as_of: str) -> pd.DataFrame:
    q = """
    SELECT user_id, as_of
    FROM subscriptions_profile_snapshots
    WHERE as_of = %(as_of)s
    """
    df = pd.read_sql(q, engine, params={"as_of": as_of})
    if df.empty:
        raise RuntimeError(f"Aucun snapshot trouvé pour as_of={as_of}")
    df = df.rename(columns={"as_of": "event_timestamp"})
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"])
    return df[["user_id", "event_timestamp"]]


def fetch_labels(engine, as_of: str) -> pd.DataFrame:
    # Schéma riche (period_start)
    try:
        q = """
        SELECT user_id, period_start, churn_label
        FROM labels
        WHERE period_start = %(as_of)s
        """
        labels = pd.read_sql(q, engine, params={"as_of": as_of})
        if not labels.empty:
            labels = labels.rename(columns={"period_start": "event_timestamp"})
            labels["event_timestamp"] = pd.to_datetime(labels["event_timestamp"])
            return labels[["user_id", "event_timestamp", "churn_label"]]
    except Exception:
        pass

    # Schéma simple
    q2 = "SELECT user_id, churn_label FROM labels"
    labels = pd.read_sql(q2, engine)
    if labels.empty:
        raise RuntimeError("Labels table empty.")
    labels["event_timestamp"] = pd.to_datetime(as_of)
    return labels[["user_id", "event_timestamp", "churn_label"]]


def build_training_df(as_of: str) -> pd.DataFrame:
    eng = get_sql_engine()
    entity_df = fetch_entity_df(eng, as_of)
    labels_df = fetch_labels(eng, as_of)

    store = FeatureStore(repo_path=FEAST_REPO)
    feat_df = store.get_historical_features(entity_df=entity_df, features=FEATURES).to_df()

    df = feat_df.merge(labels_df, on=["user_id", "event_timestamp"], how="inner")
    if df.empty:
        raise RuntimeError("Dataset vide après merge features/labels.")
    return df


def prep_xy(df: pd.DataFrame):
    y = df["churn_label"].astype(int).values
    X = df.drop(columns=["churn_label", "user_id", "event_timestamp"], errors="ignore")
    return X, y


def make_pipeline(df: pd.DataFrame, seed: int):
    ignored = {"user_id", "event_timestamp", "churn_label"}
    cat_cols = [c for c in df.columns if df[c].dtype == "object" and c not in ignored]
    num_cols = [c for c in df.columns if c not in cat_cols and c not in ignored]

    preproc = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cat_cols),
            ("num", "passthrough", num_cols),
        ],
        remainder="drop",
    )

    clf = RandomForestClassifier(
        n_estimators=300,
        n_jobs=-1,
        random_state=seed,
        class_weight="balanced",
        max_features="sqrt",
    )

    pipe = Pipeline(steps=[("prep", preproc), ("clf", clf)])
    return pipe, cat_cols, num_cols


@task
def train_candidate(as_of: str, seed: int) -> dict:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)

    df = build_training_df(as_of)
    X, y = prep_xy(df)
    pipe, cat_cols, num_cols = make_pipeline(df, seed=seed)

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.25, random_state=seed, stratify=y
    )

    with mlflow.start_run(run_name=f"candidate_{as_of}") as run:
        t0 = time.time()
        pipe.fit(X_train, y_train)
        train_time = time.time() - t0

        y_val_proba = pipe.predict_proba(X_val)[:, 1]
        y_val_pred = pipe.predict(X_val)

        val_auc = roc_auc_score(y_val, y_val_proba)
        val_f1 = f1_score(y_val, y_val_pred)
        val_acc = accuracy_score(y_val, y_val_pred)

        mlflow.log_param("as_of", as_of)
        mlflow.log_param("seed", seed)
        mlflow.log_param("model_type", "RandomForestClassifier")
        mlflow.log_metric("val_auc", float(val_auc))
        mlflow.log_metric("val_f1", float(val_f1))
        mlflow.log_metric("val_acc", float(val_acc))
        mlflow.log_metric("train_time_s", float(train_time))

        mlflow.log_dict(
            {"categorical_cols": cat_cols, "numeric_cols": num_cols},
            "feature_schema.json",
        )

        # Log + register
        mlflow.sklearn.log_model(
            sk_model=pipe,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )

    # ⚠️ get_latest_versions(stages=["None"]) est déprécié (stages vont disparaître)
    # Ici on récupère la dernière version (ordre croissant) et on prend la plus récente.
    client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    if not versions:
        raise RuntimeError("Aucune version trouvée dans le registry après log_model.")
    candidate_version = max(int(v.version) for v in versions)

    return {
        "candidate_version": str(candidate_version),
        "val_auc": float(val_auc),
        "val_f1": float(val_f1),
        "val_acc": float(val_acc),
    }


@task
def evaluate_production(as_of: str) -> dict:
    """
    Évalue le modèle Production sur le dataset du mois `as_of`.
    Retourne TOUJOURS un dict, même si load échoue.
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

    df = build_training_df(as_of)
    X, y = prep_xy(df)

    client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)
    latest_prod = client.get_latest_versions(MODEL_NAME, stages=["Production"])
    if not latest_prod:
        # Dans un TP, tu peux choisir de "promote si pas de prod"
        # Ici on renvoie un dict exploitable.
        return {
            "prod_version": None,
            "prod_auc": float("nan"),
            "prod_f1": float("nan"),
            "prod_acc": float("nan"),
        }

    prod_version = latest_prod[0].version

    try:
        prod_model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}/Production")
    except MlflowException as e:
        print(f"[WARN] Cannot load Production model artifacts: {e}")
        return {
            "prod_version": prod_version,
            "prod_auc": float("nan"),
            "prod_f1": float("nan"),
            "prod_acc": float("nan"),
        }

    # Probabilities + metrics
    y_proba = prod_model.predict_proba(X)[:, 1]
    y_pred = prod_model.predict(X)

    prod_auc = roc_auc_score(y, y_proba)
    prod_f1 = f1_score(y, y_pred)
    prod_acc = accuracy_score(y, y_pred)

    return {
        "prod_version": prod_version,
        "prod_auc": float(prod_auc),
        "prod_f1": float(prod_f1),
        "prod_acc": float(prod_acc),
    }


@task
def compare_and_promote(candidate: dict, production: dict, delta: float) -> str:
    new_auc = float(candidate["val_auc"])
    prod_auc = production.get("prod_auc")

    # Si prod_auc est NaN ou None -> pas comparable
    if prod_auc is None or (isinstance(prod_auc, float) and pd.isna(prod_auc)):
        print("[COMPARE] prod_auc unavailable (None/NaN) -> skip promotion decision.")
        return "skipped_no_prod_metric"

    prod_auc = float(prod_auc)

    print(f"[COMPARE] candidate_auc={new_auc:.4f} vs prod_auc={prod_auc:.4f} (delta={delta:.4f})")

    decision = "skipped"
    if should_promote(new_auc=new_auc, prod_auc=prod_auc, delta=delta):
        client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)
        client.transition_model_version_stage(
            name=MODEL_NAME,
            version=candidate["candidate_version"],
            stage="Production",
            archive_existing_versions=True,
        )
        decision = "promoted"

    print(f"[DECISION] {decision}")
    return decision


@flow(name="train_and_compare")
def train_and_compare_flow(as_of: str, seed: int = 42, delta: float = 0.01):
    """
    Entraîne un modèle candidat sur as_of, évalue Production sur as_of, compare val_auc et promeut si nécessaire.
    """
    cand = train_candidate(as_of, seed)
    prod = evaluate_production(as_of)
    decision = compare_and_promote(cand, prod, delta)

    prod_v = prod.get("prod_version")
    prod_auc = prod.get("prod_auc")

    print(
        f"[SUMMARY] as_of={as_of} cand_v={cand['candidate_version']} "
        f"cand_auc={cand['val_auc']:.4f} prod_v={prod_v} prod_auc={prod_auc} -> {decision}"
    )
    return decision


if __name__ == "__main__":
    train_and_compare_flow(as_of="2024-02-29", seed=42, delta=0.01)
