from feast import Entity

# Entité principale "user"
user = Entity(
    name="user",
    join_keys=["user_id"],
    description="Utilisateur StreamFlow identifié de manière unique par user_id.",
)
