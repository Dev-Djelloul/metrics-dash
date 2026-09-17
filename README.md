# 📊 metrics-dash

Dashboard de métriques e-commerce branché sur Stripe (mode test), construit
pour apprendre les techniques d'analyse de données ("BI") qu'on retrouve
dans des outils comme Power BI ou Tableau — implémentées ici à la main en
Python, sans librairie de data science, pour que la logique reste lisible.

## Fonctionnalités

- **Vue d'ensemble** — CA total, nombre de commandes, panier moyen, top clients
- **Croissance** — variation MoM (Month-over-Month) et YoY (Year-over-Year),
  moyenne mobile 7 jours pour lisser le bruit journalier
- **Segmentation RFM** — score chaque client sur Récence / Fréquence / Montant
  et l'assigne à un segment (Champions, Clients fidèles, À risque, Perdus...)
- **Analyse de cohortes** — heatmap de rétention : combien de clients d'un
  mois donné achètent encore N mois plus tard
- **Prévision** — projection du CA des prochains mois par régression
  linéaire simple (calculée à la main, sans numpy/pandas)
- **Mode démo** — un jeu de données simulé sur 8 mois avec des profils de
  clients réalistes (champions, fidèles, churnés, one-time), pour voir les
  analyses temporelles fonctionner pleinement même avec un compte Stripe
  test qui n'a que quelques charges

## Démarrage

```bash
git clone <url-du-repo>
cd metrics-dash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Édite `.env` et colle ta clé Stripe **de test** (`sk_test_...`), trouvable sur
https://dashboard.stripe.com/test/apikeys

```bash
.venv/bin/uvicorn main:app --reload --port 8420
```

Ouvre http://localhost:8420 — coche "Mode démo" en haut de la page si tu
veux explorer les analyses avec un jeu de données riche sans toucher à
Stripe.

## Générer des données de test dans Stripe (optionnel)

```bash
.venv/bin/python scripts/seed_stripe.py
```

Crée 10 clients et jusqu'à 30 charges fictives dans ton compte Stripe **test
uniquement** (le script refuse de s'exécuter si la clé n'est pas `sk_test_...`).
Limite à connaître : l'API Stripe ne permet pas de choisir une date de
création dans le passé, donc ces charges sont toutes datées d'aujourd'hui —
utile pour enrichir le RFM, pas pour les cohortes/tendances (voir le mode démo
pour ça).

## Architecture

```
main.py            → routes FastAPI, cache mémoire des appels Stripe (60s)
stripe_metrics.py  → appels à l'API Stripe + métriques de base (CA, panier moyen...)
analytics.py       → techniques "BI" : croissance, RFM, cohortes, prévision
demo_data.py       → générateur de données simulées pour le mode démo
static/index.html  → frontend HTML/JS/Chart.js, sans framework ni build step
scripts/seed_stripe.py → script pour peupler ton compte Stripe test
```

Toutes les fonctions d'`analytics.py` prennent en entrée le même format de
donnée (`{"customer": str, "amount": float, "created": datetime}`), qu'elle
vienne de vraies charges Stripe ou du générateur de démo — ce qui permet de
brancher les deux sources sur les mêmes analyses.

## Concepts appris dans ce projet

| Technique | Où | Ce que ça répond |
|---|---|---|
| MoM / YoY growth | `analytics.growth_metrics` | Est-ce qu'on croît vs le mois dernier / l'an dernier ? |
| Moyenne mobile | `analytics.growth_metrics` | Quelle est la vraie tendance derrière le bruit journalier ? |
| RFM segmentation | `analytics.rfm_segments` | Qui sont mes meilleurs clients, qui est en train de partir ? |
| Cohort retention | `analytics.cohort_retention` | Mes clients reviennent-ils acheter dans le temps ? |
| Régression linéaire | `analytics.forecast_revenue` | À quoi peut ressembler le CA des prochains mois ? |

## Pour aller plus loin

- Ajouter d'autres métriques (MRR/churn si tu passes à Stripe Subscriptions)
- Utiliser `stripe listen` pour recevoir des webhooks en temps réel plutôt
  que de re-fetcher à chaque chargement de page
- Ajouter un filtre de période interactif (date range picker) sur le dashboard
- Exporter les données en CSV depuis chaque onglet
- Remplacer la régression linéaire par une méthode plus robuste (moyenne
  mobile pondérée, décomposition saisonnière) une fois plus de données réelles

## Stack

Python · FastAPI · Stripe API · Chart.js — aucun framework frontend,
aucune librairie de data science : tout le calcul est fait à la main pour
rester pédagogique.
