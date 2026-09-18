# 📊 metrics-dash

Dashboard de métriques e-commerce branché sur Stripe (mode test), construit
pour apprendre les techniques d'analyse de données ("BI") qu'on retrouve
dans des outils comme Power BI ou Tableau — implémentées ici à la main en
Python, sans librairie de data science, pour que la logique reste lisible.

## Fonctionnalités

- **Vue d'ensemble** — CA total, nombre de commandes, panier moyen, carte
  du CA par pays de facturation (choropleth, zoomable), CA par jour, CA par
  jour de la semaine, concentration du CA (principe de Pareto), top clients
- **Croissance** — variation MoM (Month-over-Month) et YoY (Year-over-Year),
  moyenne mobile 7 jours pour lisser le bruit journalier, répartition
  nouveaux clients vs clients récurrents par mois
- **Segmentation RFM** — score chaque client sur Récence / Fréquence / Montant
  et l'assigne à un segment (Champions, Clients fidèles, À risque, Perdus...),
  LTV moyen, délai moyen entre deux achats, % de clients à risque/perdus
- **Analyse de cohortes** — heatmap de rétention : combien de clients d'un
  mois donné achètent encore N mois plus tard
- **Prévision** — projection du CA des prochains mois par régression
  linéaire simple (calculée à la main, sans numpy/pandas)
- **Lexique** — définitions de tous les termes techniques utilisés dans le
  dashboard (MoM, YoY, RFM, cohorte, Pareto, LTV, churn, API, CSV...)
- **Export CSV / Power BI** — export de chaque table clé (CA mensuel/
  quotidien, RFM, cohortes) en CSV, importable directement dans Power BI ou
  Excel sans code (popup accessible depuis le header)
- **Filtre de période** — deux champs date dans la barre d'outils pour
  restreindre tous les onglets à une plage de dates précise
- **Mode démo** — un jeu de données simulé sur 8 mois avec des profils de
  clients réalistes (champions, fidèles, churnés, one-time) et des pays
  de facturation variés, pour voir les analyses temporelles et
  géographiques fonctionner pleinement même avec un compte Stripe test qui
  n'a que quelques charges
- **Thème clair/sombre** — bascule persistée en local, design system
  dédié (`static/design-system.css`)

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
main.py                  → routes FastAPI, cache mémoire des appels Stripe (60s), export CSV
stripe_metrics.py        → appels à l'API Stripe + normalisation des charges en records
analytics.py             → techniques "BI" : croissance, RFM, cohortes, prévision, géo, fidélité
demo_data.py             → générateur de données simulées pour le mode démo
static/index.html        → frontend HTML/JS/Chart.js, sans framework ni build step
static/design-system.css → variables CSS, palette dark/light, composants (cards, badges, tabs...)
scripts/seed_stripe.py   → script pour peupler ton compte Stripe test
```

Toutes les fonctions d'`analytics.py` prennent en entrée le même format de
donnée (`{"customer": str, "amount": float, "created": datetime, "country": str | None}`),
qu'elle vienne de vraies charges Stripe ou du générateur de démo — ce qui
permet de brancher les deux sources sur les mêmes analyses. Le filtre de
période (`analytics.filter_by_date`) s'applique en amont, dans
`main.get_records()`, donc chaque route bénéficie automatiquement des
paramètres `start`/`end` sans logique dupliquée.

## Concepts appris dans ce projet

| Technique | Où | Ce que ça répond |
|---|---|---|
| MoM / YoY growth | `analytics.growth_metrics` | Est-ce qu'on croît vs le mois dernier / l'an dernier ? |
| Moyenne mobile | `analytics.growth_metrics` | Quelle est la vraie tendance derrière le bruit journalier ? |
| RFM segmentation | `analytics.rfm_segments` | Qui sont mes meilleurs clients, qui est en train de partir ? |
| Cohort retention | `analytics.cohort_retention` | Mes clients reviennent-ils acheter dans le temps ? |
| Régression linéaire | `analytics.forecast_revenue` | À quoi peut ressembler le CA des prochains mois ? |
| Principe de Pareto (80/20) | `analytics.revenue_concentration` | Une petite part de mes clients fait-elle une grande part du CA ? |
| Nouveaux vs récurrents | `analytics.new_vs_returning_by_month` | Est-ce que je croîs par acquisition ou par fidélisation ? |
| LTV (Lifetime Value) | `analytics.loyalty_metrics` | Combien un client rapporte-t-il en moyenne, pour savoir combien dépenser en acquisition ? |
| Choropleth géographique | `analytics.revenue_by_country` | Où sont mes clients, et où le CA se concentre-t-il ? |

## Déploiement sur Cloudflare (Containers)

L'app tourne sur [Cloudflare Containers](https://developers.cloudflare.com/containers/) :
un petit Worker (`src/index.ts`) route toutes les requêtes vers un conteneur
Docker qui fait tourner `main.py` tel quel — aucune réécriture du backend
Python n'est nécessaire.

```
static/index.html ──┐
main.py (FastAPI)  ──┼── Dockerfile ──► Cloudflare Container ◄── src/index.ts (Worker, routeur)
analytics.py, etc. ──┘
```

**Prérequis** : un compte Cloudflare, Node.js, et `npm install` à la racine
du repo (installe `wrangler` et `@cloudflare/containers`).

```bash
npm install
npx wrangler login

# clé Stripe stockée comme secret Cloudflare, jamais commitée
npx wrangler secret put STRIPE_SECRET_KEY

npx wrangler deploy
```

`wrangler deploy` construit l'image Docker à partir du `Dockerfile` à la
racine, la pousse sur le registre de conteneurs de Cloudflare, et déploie
le Worker qui route vers elle.

Pour tester en local avant de déployer :

```bash
npx wrangler dev
```

**Points à surveiller** (Cloudflare Containers est une fonctionnalité
encore jeune, sortie en 2025) :
- `sleepAfter = "10m"` dans `src/index.ts` éteint le conteneur après 10 min
  d'inactivité pour limiter les coûts — le premier appel après une pause
  aura un temps de démarrage ("cold start") plus long.
- `max_instances = 1` dans `wrangler.toml` : une seule instance de conteneur,
  cohérent avec un usage perso/démo (pas de montée en charge prévue).
- Le cache mémoire des appels Stripe (`_cache` dans `main.py`, 60s) est
  perdu à chaque redémarrage du conteneur — normal, ce n'est qu'un cache.

## Pour aller plus loin

Deux pistes volontairement pas codées : elles supposent un choix produit
que seul toi peux trancher, pas juste une brique technique à ajouter.

- **MRR/churn si tu passes à Stripe Subscriptions** — ça suppose de
  facturer en abonnement (pas seulement des charges ponctuelles), ce qui
  change le modèle de données Stripe à la base (`stripe.Subscription`
  plutôt que `stripe.Charge`)
- **Webhooks temps réel** (`stripe listen`) plutôt que le cache 60s actuel
  — demande un secret webhook supplémentaire et une logique de réception
  d'événements ; pertinent surtout si le dashboard doit refléter les
  changements en quelques secondes plutôt qu'en une minute

Une piste qui attend d'avoir plus de données réelles pour être utile :

- Remplacer la régression linéaire par une méthode plus robuste (moyenne
  mobile pondérée, décomposition saisonnière) — sur peu de mois d'historique,
  une méthode plus complexe n'est pas forcément plus fiable

## Stack

Python · FastAPI · Stripe API · Chart.js (+ chartjs-chart-geo pour la carte,
chartjs-plugin-zoom pour son zoom) — aucun framework frontend, aucune
librairie de data science : tout le calcul est fait à la main pour rester
pédagogique. Déployé sur Cloudflare Containers (voir plus haut).
