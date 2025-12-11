Execrice 1: 
1.a : État initial du dépôt : 
PS C:\Users\ASUS\Desktop\ML\tp1> git status
>>
On branch main
Your branch is up to date with 'origin/main'.

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        reports/rapport_tp2.md

nothing added to commit but untracked files present (use "git add" to track)

1.b : PS C:\Users\ASUS\Desktop\ML\tp1> ls
    Répertoire : C:\Users\ASUS\Desktop\ML\tp1
Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
d-----        08/12/2025     13:31                api
d-----        11/12/2025     14:48                data
d-----        11/12/2025     14:48                db
d-----        11/12/2025     14:44                reports
d-----        11/12/2025     14:48                services
-a----        08/12/2025     13:35            538 docker-compose.yml

1.c :  Structure des données
PS C:\Users\ASUS\Desktop\ML\tp1> ls data/seeds/month_000
    Répertoire : C:\Users\ASUS\Desktop\ML\tp1\data\seeds\month_000
Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
-a----        11/12/2025     14:51         117882 labels.csv
-a----        11/12/2025     14:51          91587 payments_agg_90d.csv
-a----        11/12/2025     14:51         425583 subscriptions.csv
-a----        11/12/2025     14:51         123303 support_agg_90d.csv
-a----        11/12/2025     14:51         376828 usage_agg_30d.csv
-a----        11/12/2025     14:51         317330 users.csv

PS C:\Users\ASUS\Desktop\ML\tp1> ls .\data\seeds\month_001 
    Répertoire : C:\Users\ASUS\Desktop\ML\tp1\data\seeds\month_001
Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
-a----        11/12/2025     14:51         118050 labels.csv
-a----        11/12/2025     14:51          91587 payments_agg_90d.csv
-a----        11/12/2025     14:51         425583 subscriptions.csv
-a----        11/12/2025     14:51         123303 support_agg_90d.csv
-a----        11/12/2025     14:51         376948 usage_agg_30d.csv
-a----        11/12/2025     14:51         317330 users.csv

Exercice 2 : 
2.a : Le fichier db/init/001_schema.sql a été créé et contient le schéma complet utilisé pour initialiser la base PostgreSQL lors du démarrage du conteneur.
2.b : Le fichier .env stocke les variables d’environnement sensibles (comme les identifiants PostgreSQL). Docker Compose les injecte automatiquement dans les conteneurs pour séparer la configuration du code.
2.d : La commande \dt montre les tables créées automatiquement grâce au script 001_schema.sql.
streamflow=# \dt
               List of relations
 Schema |       Name       | Type  |   Owner    
--------+------------------+-------+------------
 public | labels           | table | streamflow
 public | payments_agg_90d | table | streamflow
 public | subscriptions    | table | streamflow
 public | support_agg_90d  | table | streamflow
 public | usage_agg_30d    | table | streamflow
 public | users            | table | streamflow
(6 rows)

users : informations de base sur les utilisateurs
subscriptions : détails d’abonnement
usage_agg_30d : activité moyenne 30 jours
payments_agg_90d : paiements échoués 90 jours
support_agg_90d : tickets support 90 jours
labels : étiquette de churn

Exercice 3 : 
3.a : Le conteneur prefect sert à l’orchestration : il héberge le serveur Prefect + un worker qui exécute le flow d’ingestion (lecture des CSV et écriture dans PostgreSQL).
3.b : La fonction upsert_csv lit un CSV, crée une table temporaire, y insère les données puis exécute un INSERT ... ON CONFLICT DO UPDATE pour effectuer un upsert : si la clé primaire existe déjà, les colonnes non-PK sont mises à jour, sinon une nouvelle ligne est créée.
3.c: 
streamflow=# SELECT COUNT(*) FROM users;
 count 
-------
  7043
(1 row)

streamflow=# SELECT COUNT(*) FROM subscriptions;
 count 
-------
  7043
(1 row)
Après ingestion de month_000, la table users contient 7043 lignes et la table subscriptions 7043 lignes.
Cela correspond au nombre de clients présents après le mois 000.

Exercice 4 : 
4.a : Validation des données :
La fonction validate_with_ge applique des règles Great Expectations sur les tables chargées (schéma, valeurs non nulles, bornes minimales).
Si une expectation échoue, elle lève une exception et fait échouer le flow Prefect.
Cela agit comme un garde-fou : on bloque le pipeline si les données sont incomplètes, corrompues ou incohérentes, avant d’entraîner un modèle ou de créer d’autres artefacts.
Pour usage_agg_30d, nous forçons les colonnes watch_hours_30d, avg_session_mins_7d, unique_devices_30d, skips_7d et rebuffer_events_7d à être ≥ 0, car ce sont des compteurs ou durées qui ne peuvent pas être négatifs.
Ces bornes permettent de détecter des données corrompues ou incohérentes et protègent le modèle en évitant d’entraîner sur des valeurs impossibles.

Exercice 5 : 
5.a : La fonction snapshot_month(as_of) crée (si besoin) les tables de snapshots et copie l’état des tables live vers des tables *_snapshots en ajoutant une colonne as_of. Cela permet de figer l’état des données à la fin de chaque mois et de conserver l’historique temporel.
5.b : streamflow=# 
streamflow=# SELECT COUNT(*) FROM subscriptions_profile_snapshots WHERE as_of = '2024-01-31';
 count 
-------
  7043
(1 row)

streamflow=# SELECT COUNT(*) FROM subscriptions_profile_snapshots WHERE as_of = '2024-02-29';
 count 
-------
  7043
(1 row)

streamflow=# SELECT DISTINCT as_of FROM subscriptions_profile_snapshots;
   as_of    
------------
 2024-02-29
 2024-01-31
(2 rows)
Après ingestion des mois month_000 et month_001, la table subscriptions_profile_snapshots contient 7043 lignes pour chacune des deux dates.
Cela montre que la population d’utilisateurs avec un profil de souscription reste stable entre fin janvier et fin février dans ce jeu de données. En revanche, même si le nombre de lignes est identique, les valeurs peuvent évoluer: les snapshots figent l’état des abonnements à chaque fin de mois, ce qui permet ensuite d’analyser l’évolution temporelle sans toucher aux tables live.

5.c : synthèse :

Pourquoi on ne travaille pas directement sur les tables live ?
Les tables live évoluent en permanence. Entraîner un modèle dessus rend la reproduction d’une expérience impossible et peut introduire de la data leakage (le modèle “voit” des infos futures).

Pourquoi les snapshots sont importants ?
Les snapshots figent l’état des features à une date as_of, ce qui permet de reproduire les entraînements, de respecter la chronologie des données et d’éviter que le modèle utilise des informations qui n’étaient pas disponibles à l’époque.

Réflexion perso 
Le plus difficile a été de bien gérer l’upsert et les ON CONFLICT. J’ai aussi rencontré des erreurs de type “relation does not exist”, corrigées en vérifiant l’ordre d’exécution du DDL et des INSERT, ainsi que les noms de tables.

Schéma montrant le pipeline complet : 
Le pipeline commence par l’ingestion des CSV via upsert_csv, puis applique une validation des données avec Great Expectations (validate_with_ge). Si la validation réussit, nous créons des snapshots temporels via snapshot_month(as_of) pour figer l’état des données à la fin de chaque mois. Ces snapshots garantissent la reproductibilité temporelle et empêchent le data leakage lors du futur entraînement des modèles.

        +----------------------+
        |   CSV mensuels       |
        |  (month_000 / 001)   |
        +----------+-----------+
                   |
                   v
        +----------------------+
        |  ingest_month_flow() |
        +----------+-----------+
                   |
                   v
        +----------------------+
        |   upsert_csv()       |
        |   → tables live      |
        +----------+-----------+
                   |
                   v
        +----------------------+
        | validate_with_ge()   |
        |   → contrôle qualité |
        +----------+-----------+
                   |
                   v
        +----------------------+
        |  snapshot_month()    |
        |   → *_snapshots      |
        +----------+-----------+
                   |
                   v
        +----------------------+
        |   PostgreSQL final   |
        |  Tables live + snaps |
        +----------------------+



