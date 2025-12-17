import os
import time
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
from sqlalchemy import create_engine

from feast import FeatureStore

from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, roc_auc_score, accuracy_score
from sklearn.ensemble import RandomForestClassifier
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.pipeline import Pipeline

import mlflow
import mlflow.sklearn
from mlflow.models import ModelSignature
from mlflow.types.schema import Schema, ColSpec

# --------------------
# Config
# --------------------
FEAST_REPO = "/repo"
MODEL_NAME = "streamflow_churn"

AS_OF = os.environ.get("TRAIN_AS_OF", "2024-01-31")

PG_USER = os.environ.get("POSTGRES_USER", "streamflow")
PG_PWD = os.environ.get("POSTGRES_PASSWORD", "streamflow")
PG_DB = os.environ.get("POSTGRES_DB", "streamflow")
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT", "streamflow")


# --------------------
# Helpers
# --------------------
def get_sql_engine():
    uri = f"postgresql+psycopg2://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
    return create_engine(uri)


def fetch_entity_df(engine, as_of):
    q = """
    SELECT user_id, as_of
    FROM subscriptions_profile_snapshots
    WHERE as_of = %(as_of)s
    """
    df = pd.read_sql(q, engine, params={"as_of": as_of})
    if df.empty:
        raise RuntimeError(f"No snapshot rows found at as_of={as_of}")
    df = df.rename(columns={"as_of": "event_timestamp"})
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"])
    return df[["user_id", "event_timestamp"]]


def fetch_labels(engine, as_of):
    # 1) si labels contient period_start
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

    # 2) fallback table labels(user_id, churn_label)
    q2 = "SELECT user_id, churn_label FROM labels"
    labels = pd.read_sql(q2, engine)
    if labels.empty:
        raise RuntimeError("Labels table is empty.")
    labels["event_timestamp"] = pd.to_datetime(as_of)
    return labels[["user_id", "event_timestamp", "churn_label"]]


def build_training_set(store, entity_df, features):
    hf = store.get_historical_features(
        entity_df=entity_df,
        features=features,
    )
    return hf.to_df()


def prep_xy(df, label_col="churn_label"):
    y = df[label_col].astype(int).values
    X = df.drop(columns=[label_col, "user_id", "event_timestamp"], errors="ignore")
    return X, y


# --------------------
# Main
# --------------------
def main():
    # TODO 1: MLflow setup
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)

    engine = get_sql_engine()
    entity_df = fetch_entity_df(engine, AS_OF)
    labels = fetch_labels(engine, AS_OF)

    # TODO 2: features Feast
    features = [
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

    store = FeatureStore(repo_path=FEAST_REPO)
    feat_df = build_training_set(store, entity_df, features)

    # TODO 3: merge features + labels
    df = feat_df.merge(labels, on=["user_id", "event_timestamp"], how="inner")

    if df.empty:
        raise RuntimeError("Training set is empty after merge. Check AS_OF and labels.")

    # Feature engineering minimal
    cat_cols = [
        c for c in df.columns
        if df[c].dtype == "object" and c not in ["user_id", "event_timestamp"]
    ]
    num_cols = [
        c for c in df.columns
        if c not in cat_cols + ["user_id", "event_timestamp", "churn_label"]
    ]

    X, y = prep_xy(df)

    print("cat_cols =", cat_cols)

    # TODO 4: preprocessing
    preproc = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat_cols),
            ("num", "passthrough", num_cols),
        ],
        remainder="drop"
    )

    # TODO 5: model
    clf = RandomForestClassifier(
        n_estimators=300,
        n_jobs=-1,
        random_state=42,
        class_weight="balanced",
        max_features="sqrt",
    )

    # TODO 6: pipeline
    pipe = Pipeline(steps=[("prep", preproc), ("clf", clf)])

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.25, random_state=42, stratify=y
    )

    # TODO 7: MLflow run + log
    with mlflow.start_run(run_name=f"rf_baseline_{AS_OF}") as run:
        start = time.time()
        pipe.fit(X_train, y_train)
        train_time = time.time() - start

        if hasattr(pipe, "predict_proba"):
            y_val_proba = pipe.predict_proba(X_val)[:, 1]
            auc = roc_auc_score(y_val, y_val_proba)
        else:
            auc = float("nan")

        y_val_pred = pipe.predict(X_val)
        f1 = f1_score(y_val, y_val_pred)
        acc = accuracy_score(y_val, y_val_pred)

        mlflow.log_param("as_of", AS_OF)
        mlflow.log_param("model_type", "RandomForestClassifier")
        mlflow.log_param("n_estimators", 300)
        mlflow.log_param("class_weight", "balanced")
        mlflow.log_param("max_features", "sqrt")
        mlflow.log_param("random_state", 42)

        mlflow.log_metric("val_auc", float(auc) if auc == auc else np.nan)
        mlflow.log_metric("val_f1", float(f1))
        mlflow.log_metric("val_acc", float(acc))
        mlflow.log_metric("train_time_sec", float(train_time))
        mlflow.log_metric("train_rows_after_merge", int(len(df)))

        # TODO 7bis: artefact schema
        mlflow.log_dict(
            {"categorical_cols": cat_cols, "numeric_cols": num_cols},
            "feature_schema.json"
        )

        # TODO 8: signature + registry
        input_schema = Schema(
            [
                ColSpec("long", "months_active"),
                ColSpec("double", "monthly_fee"),
                ColSpec("boolean", "paperless_billing"),
                ColSpec("boolean", "plan_stream_tv"),
                ColSpec("boolean", "plan_stream_movies"),
                ColSpec("string", "net_service"),
                ColSpec("double", "watch_hours_30d"),
                ColSpec("double", "avg_session_mins_7d"),
                ColSpec("long", "unique_devices_30d"),
                ColSpec("long", "skips_7d"),
                ColSpec("long", "rebuffer_events_7d"),
                ColSpec("long", "failed_payments_90d"),
                ColSpec("long", "support_tickets_90d"),
                ColSpec("double", "ticket_avg_resolution_hrs_90d"),
            ]
        )
        output_schema = Schema([ColSpec("long", "prediction")])
        signature = ModelSignature(inputs=input_schema, outputs=output_schema)

        # TODO 9: on log "pipe" (préproc + modèle) pour éviter un skew au serving
        mlflow.sklearn.log_model(
            sk_model=pipe,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
            signature=signature
        )

        print(
            f"[OK] Trained baseline RF. AUC={auc:.4f} F1={f1:.4f} ACC={acc:.4f} "
            f"(run_id={run.info.run_id})"
        )


if __name__ == "__main__":
    main()
