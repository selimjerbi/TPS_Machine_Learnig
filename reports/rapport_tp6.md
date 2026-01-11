Exercice 1 : 
PS C:\Users\ASUS\Desktop\ML\tp1> docker compose up -d
[+] Running 7/7
 ✔ Container tp1-postgres-1         Running                                                                                                                                              0.0s 
 ✔ Container tp1-mlflow-1           Running                                                                                                                                              0.0s 
 ✔ Container tp1-feast-1            Running                                                                                                                                              0.0s 
 ✔ Container tp1-prefect-1          Running                                                                                                                                              0.0s 
 ✔ Container tp1-api-1              Running                                                                                                                                              0.0s 
 ✔ Container streamflow-prometheus  Running                                                                                                                                              0.0s 
 ✔ Container streamflow-grafana     Running                                                                                                                                              0.0s 
PS C:\Users\ASUS\Desktop\ML\tp1> docker compose ps
NAME                    IMAGE                           COMMAND                  SERVICE      CREATED       STATUS         PORTS
streamflow-grafana      grafana/grafana:11.2.0          "/run.sh"                grafana      2 weeks ago   Up 5 minutes   0.0.0.0:3000->3000/tcp, [::]:3000->3000/tcp
streamflow-prometheus   prom/prometheus:v2.55.1         "/bin/prometheus --c…"   prometheus   2 weeks ago   Up 5 minutes   0.0.0.0:9090->9090/tcp, [::]:9090->9090/tcp
tp1-api-1               tp1-api                         "uvicorn app:app --h…"   api          2 weeks ago   Up 5 minutes   0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
tp1-feast-1             tp1-feast                       "bash -lc 'tail -f /…"   feast        2 weeks ago   Up 5 minutes   
tp1-mlflow-1            ghcr.io/mlflow/mlflow:v2.16.0   "mlflow server --bac…"   mlflow       2 weeks ago   Up 5 minutes   0.0.0.0:5000->5000/tcp, [::]:5000->5000/tcp
tp1-postgres-1          postgres:16                     "docker-entrypoint.s…"   postgres     2 weeks ago   Up 5 minutes   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
tp1-prefect-1           tp1-prefect                     "/usr/bin/tini -g --…"   prefect      2 weeks ago   Up 5 minutes

Exercice 2 :
PS C:\Users\ASUS\Desktop\ML\tp1> pytest -q
..                                                                                                                                                                                     [100%]
2 passed in 0.15s

On extrait une fonction pure (sans dépendances Prefect/MLflow, sans I/O) pour pouvoir tester la logique de décision rapidement, de façon déterministe, et sans infrastructure externe.

Exercice 3 : 

![alt text](image-35.png)

Capture MLFLOW : 
![alt text](image-36.png)

 On impose un delta pour éviter de promouvoir un modèle pour un gain trop faible (souvent dû au hasard du split / bruit statistique), et pour stabiliser la production en ne déployant que des améliorations significatives.

 Exercice 4 : 
 Rapport Drift : 
 ![alt text](image-37.png)

[Evidently] ... drift_share=0.06 -> RETRAINING_TRIGGERED drift_share=0.06 >= 0.02 -> skipped
[DECISION] skipped

Exercice 5 :
API – Reload du modèle & prédiction
Appel /predict : 

```bash
curl -s -X POST "http://localhost:8000/predict" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"<UN_USER_ID_REEL>"}'

```

user_id    prediction features
-------    ---------- --------
7590-VHVEG          1 @{user_id=7590-VHVEG; months_active=1; plan_stream_movies=False; paperless_billing=True; plan_stream_tv=False; net_service=DSL; monthly_fee=29,850000381469727; skip...

L’API charge le modèle MLflow au démarrage (models:/streamflow_churn/Production) et le garde en mémoire, après une promotion dans le Registry, il faut redémarrer le service pour recharger la nouvelle version Production.

Exercice 6 :
![alt text](image-38.png)

On démarre Docker Compose dans la CI pour valider l’intégration multi-services (DB/Feast/MLflow/API) et vérifier que l’API démarre correctement et répond au healthcheck, ce que des tests unitaires seuls ne couvrent pas.

## Synthèse – Monitoring, réentraînement et CI/CD

Dans ce projet, le drift des données est mesuré à l’aide d’Evidently en comparant une période de référence à une période courante. Le principal indicateur utilisé est le drift_share, qui représente la proportion de features présentant un drift statistiquement significatif. Un seuil de déclenchement est fixé à 0.02 afin d’automatiser la décision de réentraînement. Dans un contexte réel, ce seuil serait généralement plus élevé afin d’éviter des réentraînements trop fréquents dus au bruit ou à des variations normales des données.

Lorsque le drift dépasse le seuil, le flow train_and_compare_flow est déclenché. Ce flow construit un dataset cohérent à une date as_of, entraîne un modèle candidat, et évalue ses performances sur un jeu de validation via la métrique val_auc. En parallèle, le modèle actuellement en Production est évalué sur exactement le même split de données. La décision de promotion repose sur une règle simple et testée unitairement : le modèle candidat est promu uniquement si son val_auc dépasse celui du modèle en Production d’au moins un delta. Ce delta permet d’éviter des promotions dues à des gains marginaux ou aléatoires.

Les responsabilités sont clairement séparées entre les outils :

Prefect orchestre les workflows métier (monitoring, entraînement, comparaison, promotion) et gère la logique MLOps dynamique.

GitHub Actions assure la CI : exécution des tests unitaires rapides et vérification que la stack Docker démarre correctement via un healthcheck. Aucun entraînement complet n’y est exécuté.

## Limites et améliorations

La CI ne doit pas entraîner le modèle complet, car l’entraînement est coûteux, lent et non déterministe, ce qui rendrait les pipelines instables et difficiles à maintenir.
Plusieurs tests manquent encore, notamment des tests d’intégration fonctionnels sur les flows Prefect, des tests de non-régression sur les métriques, et des tests de robustesse sur les données en entrée.
Enfin, en production réelle, une approbation humaine est souvent nécessaire avant toute promotion : gouvernance ML, contraintes réglementaires, validation métier et analyse d’impact sont essentielles pour éviter des déploiements automatiques risqués.