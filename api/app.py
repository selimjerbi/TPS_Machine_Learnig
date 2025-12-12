from fastapi import FastAPI
from feast import FeatureStore

app = FastAPI()
store = FeatureStore(repo_path="/repo")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/features/{user_id}")
def get_features(user_id: str):
    features = [
        "subs_profile_fv:months_active",
        "subs_profile_fv:monthly_fee",
        "subs_profile_fv:paperless_billing",
    ]

    feature_dict = store.get_online_features(
        features=features,
        entity_rows=[{"user_id": user_id}],
    ).to_dict()

    # On enlève "user_id" si Feast le renvoie, et on simplifie en scalaires
    feature_dict.pop("user_id", None)
    simple = {name.split(":")[-1]: values[0] for name, values in feature_dict.items()}

    return {"user_id": user_id, "features": simple}
