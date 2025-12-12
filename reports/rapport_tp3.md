# Contexte

À l’issue des TP précédents, nous disposons d’un pipeline de données fonctionnel permettant d’ingérer des fichiers CSV mensuels dans une base PostgreSQL, de valider les données avec Great Expectations, puis de figer leur état à l’aide de snapshots temporels. Ces snapshots couvrent actuellement deux périodes (2024-01-31 et 2024-02-29) et concernent plusieurs tables métiers : utilisateurs, abonnements, usages agrégés, paiements et support client.

L’objectif de ce TP3 est de connecter ces données historisées à un Feature Store (Feast) afin de préparer l’entraînement futur d’un modèle de churn. Nous cherchons à définir des features réutilisables à partir des snapshots, à les récupérer en mode offline pour constituer un jeu de données d’entraînement, puis en mode online pour des usages temps réel. Enfin, nous exposons ces features via un endpoint API simple, illustrant leur intégration dans une architecture de production.

# Mise en place de Feast

Commande utilisée pour démarrer les services :
docker compose up -d --build

Le conteneur feast sert à exécuter Feast dans un environnement isolé (Python + dépendances Feast).
La configuration du Feature Store est stockée dans /repo à l’intérieur du conteneur (volume monté depuis ./services/feast_repo/repo).
On utilisera ensuite docker compose exec feast, pour lancer les commandes Feast (ex: feast apply, feast materialize,...).

# Définition du Feature Store

Une Entity dans Feast représente l’objet métier sur lequel on définit et récupère des features.  
Ici, user_id est un très bon choix de clé de jointure car c’est déjà la clé stable et commune à toutes nos tables (users, subscriptions, snapshots…), ce qui garantit des jointures cohérentes entre sources.

La table usage_agg_30d_snapshots contient des features comme watch_hours_30d, avg_session_mins_7d, unique_devices_30 , skips_7d.  
La colonne as_of sert de référence temporelle (timestamp_field) pour permettre à Feast de faire des jointures point-in-time correctes.

`feast apply` lit la définition du Feature Store (entities, sources, feature views) et crée/met à jour le registre Feast (registry.db).  
Ce registre est la “source de vérité” des objets Feast déclarés : il permet ensuite d’exécuter les récupérations offline/online et la matérialisation vers l’online store.

# Récupération offline & online

docker compose exec prefect python build_training_dataset.py
/usr/local/lib/python3.11/site-packages/feast/repo_config.py:278: DeprecationWarning: The serialization version below 3 are deprecated. Specifying `entity_key_serialization_version` to 3 is recommended.
  warnings.warn(
[OK] Wrote /data/processed/training_df.csv with 7043 rows

Get-Content data/processed/training_df.csv -TotalCount 5
user_id,event_timestamp,months_active,monthly_fee,paperless_billing,watch_hours_30d,avg_session_mins_7d,failed_payments_90d,churn_label
7590-VHVEG,2024-01-31,1,29.85,True,24.4836507667874,29.141044640845102,1,True
5575-GNVDE,2024-01-31,34,56.95,False,30.0362276875424,29.141044640845102,0,False
3668-QPYBK,2024-01-31,2,53.85,True,26.7068107231889,29.141044640845102,1,False
7795-CFOCW,2024-01-31,45,42.300000000000004,False,21.8920408062136,29.141044640845102,1,True

Feast garantit la cohérence temporelle grâce au `timestamp_field="as_of"` dans les `PostgreSQLSource` : chaque feature est associée à une date de snapshot.  
Dans `entity_df`, on fournit `user_id` + `event_timestamp`, ce qui permet à Feast de récupérer les valeurs valides à cette date (jointure temporelle point-in-time), évitant d’utiliser des infos “futures” pour une date donnée.

Online features for user: 0001
{'user_id': ['0001'], 'monthly_fee': [None], 'paperless_billing': [None], 'months_active': [None]}
Si on interroge un `user_id` sans features matérialisées (utilisateur inexistant ou hors fenêtre de matérialisation), Feast renvoie des valeurs `None` (pas de clé trouvée dans l’online store).

curl http://localhost:8000/features/7590-VHVEG
StatusCode        : 200
StatusDescription : OK
Content           : {"user_id":"7590-VHVEG","features":{"months_active":1,"monthly_fee":29.850000381469727,"paperless_billing":true}}
RawContent        : HTTP/1.1 200 OK
                    Content-Length: 113
                    Content-Type: application/json
                    Date: Fri, 12 Dec 2025 09:30:15 GMT
                    Server: uvicorn

                    {"user_id":"7590-VHVEG","features":{"months_active":1,"monthly_fee":29.850...
Forms             : {}
Headers           : {[Content-Length, 113], [Content-Type, application/json], [Date, Fri, 12 Dec 2025 09:30:15 GMT], [Server, uvicorn]}
Images            : {}
InputFields       : {}
Links             : {}
ParsedHtml        : mshtml.HTMLDocumentClass
RawContentLength  : 113

# Réflexion

Cet endpoint réduit le training-serving skew car il sert en production des features calculées selon la même définition (FeatureViews Feast) que celles utilisées pour construire le dataset d’entraînement via `get_historical_features`.  
On évite de recode des features à la main dans l’API (risque de divergence).  
Ainsi, une modification de feature est centralisée dans Feast et se reflète à la fois offline (training) et online (serving).
