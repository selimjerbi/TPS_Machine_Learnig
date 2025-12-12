from feast import FeatureStore

store = FeatureStore(repo_path="/repo")

# Mets un user_id existant (pris dans data/seeds/month_000/users.csv)
user_id = "0001"  # <-- à remplacer

features = [
    "subs_profile_fv:months_active",
    "subs_profile_fv:monthly_fee",
    "subs_profile_fv:paperless_billing",
]

feature_dict = store.get_online_features(
    features=features,
    entity_rows=[{"user_id": user_id}],
).to_dict()

print("Online features for user:", user_id)
print(feature_dict)
