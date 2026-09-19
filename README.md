# 📊 metrics-dash

Dashboard de métriques e-commerce branché sur Stripe, construit pour
apprendre les techniques d'analyse de données ("BI") qu'on retrouve dans des
outils comme Power BI ou Tableau — implémentées ici à la main en Python,
sans librairie de data science, pour que la logique reste lisible.

Chaque visiteur se connecte avec **son propre compte Stripe** (via Stripe
Connect OAuth) : le dashboard n'affiche jamais que ses données à lui, en
temps quasi réel. Voir [Connexion & comptes](#connexion--comptes)
ci-dessous pour le détail du flux.

## Fonctionnalités

- **Vue d'ensemble** — CA total, nombre de commandes, panier moyen, carte
  du CA par pays de facturation (choropleth, zoomable), CA par jour, CA par
  jour de la semaine, concentration du CA (principe de Pareto), top clients
- **Croissance** — variation MoM (Month-over-Month) et YoY (Year-over-Year),
  moyenne mobile 7 jours pour lisser le bruit journalier, répartition
  nouveaux clients vs clients récurrents par mois, **MRR** (revenu récurrent
  mensuel) calculé depuis tes abonnements Stripe actifs si tu factures en
  récurrent
- **Segmentation RFM** — score chaque client sur Récence / Fréquence / Montant
  et l'assigne à un segment (Champions, Clients fidèles, À risque, Perdus...),
  avec une action marketing suggérée par segment, LTV moyen, délai moyen
  entre deux achats, % de clients à risque/perdus — comparés à des repères
  indicatifs de ton secteur d'activité si renseigné
- **Analyse de cohortes** — heatmap de rétention (combien de clients d'un
  mois donné achètent encore N mois plus tard), rétention moyenne toutes
  cohortes confondues, CA généré par cohorte
- **Prévision** — projection du CA, du nombre de commandes et des nouveaux
  clients des prochains mois par régression linéaire simple (calculée à la
  main, sans numpy/pandas), avec intervalle de confiance
- **Alertes** — bandeau signalant un CA en baisse marquée d'un mois sur
  l'autre, ou une part importante de clients à risque/perdus
- **Secteur d'activité** — renseigné une fois à la connexion (Stripe ne
  transmet aucune info sur l'activité derrière les transactions), sert à
  comparer tes métriques à des repères sectoriels indicatifs
- **Multi-devise** — si ton compte encaisse dans plusieurs devises, chacune
  a son propre jeu de métriques complet (jamais mélangées ni converties)
- **Lexique** — définitions de tous les termes techniques utilisés dans le
  dashboard (MoM, YoY, RFM, cohorte, Pareto, LTV, MRR, churn, API, CSV...)
- **Export CSV / Power BI** — export de chaque table clé (CA mensuel/
  quotidien, RFM, cohortes) en CSV, importable directement dans Power BI ou
  Excel sans code (popup accessible depuis le header)
- **Filtre de période** — deux champs date dans la barre d'outils pour
  restreindre tous les onglets à une plage de dates précise
- **Mode démo** — un jeu de données simulé sur 8 mois avec des profils de
  clients réalistes (champions, fidèles, churnés, one-time) et des pays
  de facturation variés, pour voir les analyses temporelles et
  géographiques fonctionner pleinement sans connecter de compte Stripe
- **PWA** — installable sur mobile/desktop (manifest + service worker),
  app shell en cache pour un chargement rapide, jamais les données
- **Thème clair/sombre** — bascule persistée en local, design system
  dédié (`static/design-system.css`)

## Connexion & comptes

Le bouton **"Se connecter avec Stripe"** déclenche le flux OAuth de
[Stripe Connect](https://stripe.com/docs/connect) :

1. Redirection vers l'écran d'autorisation **standard de Stripe**
   (`connect.stripe.com/oauth/authorize`), qui affiche le nom de la
   plateforme et demande l'autorisation d'accéder au compte.
2. La personne se connecte avec **ses propres identifiants Stripe** (son
   compte à elle) et autorise l'accès.
3. Stripe redirige vers `/auth/callback` avec un code à usage unique,
   échangé côté serveur contre un **jeton d'accès propre à ce compte**.
4. Le jeton est stocké dans Cloudflare D1, lié à une session (cookie signé,
   `auth.py`) — aucun mot de passe à gérer.
5. Toutes les métriques affichées ensuite sont calculées en appelant l'API
   Stripe **avec ce jeton**, donc les vraies données du compte connecté,
   automatiquement, sans configuration manuelle.

**À savoir** : le scope demandé est `read_write` (pas `read_only`) — Stripe
réserve `read_only` aux plateformes validées spécifiquement sur demande.
Le code n'appelle que des endpoints Stripe en lecture (`Charge.list`,
`Subscription.list`, `Account.retrieve`), jamais d'écriture, mais le jeton
obtenu le permettrait techniquement. Compromis raisonnable pour un usage
entre quelques utilisateurs de confiance ; à reconsidérer (demander l'accès
`read_only` à Stripe) avant une ouverture plus large.

Le **mode démo** ne nécessite aucune connexion : un jeu de données simulées
est généré à la volée (`demo_data.py`).

## Démarrage

```bash
git clone <url-du-repo>
cd metrics-dash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Édite `.env` :
- `STRIPE_SECRET_KEY` : ta clé secrète **de test** (`sk_test_...`), trouvable
  sur https://dashboard.stripe.com/test/apikeys — sert à échanger les codes
  OAuth, pas à lire les données d'un utilisateur en particulier
- `STRIPE_CONNECT_CLIENT_ID` (`ca_...`) et `SESSION_SECRET` : nécessaires
  uniquement pour tester la connexion Stripe en local ; sans eux, le mode
  démo reste utilisable
- `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `CF_D1_DATABASE_ID` : nécessaires
  uniquement pour la persistance des comptes utilisateurs (D1) ; sans eux,
  au démarrage l'app log un avertissement et continue en mode démo seul

```bash
.venv/bin/uvicorn main:app --reload --port 8420
```

Ouvre http://localhost:8420 — coche "Mode démo" en haut de la page si tu
veux explorer les analyses sans connecter de compte Stripe.

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
main.py                  → routes FastAPI, auth Stripe Connect, cache mémoire (60s), export CSV
auth.py                  → jetons de session signés (HMAC), stateless (pas de table sessions)
db.py                    → client Cloudflare D1 via son API REST (table users)
stripe_metrics.py        → appels à l'API Stripe (charges, abonnements) + normalisation en records
analytics.py             → techniques "BI" : croissance, RFM, cohortes, prévision, géo, fidélité
demo_data.py             → générateur de données simulées pour le mode démo
static/index.html        → frontend HTML/JS/Chart.js, sans framework ni build step
static/design-system.css → variables CSS, palette dark/light, composants (cards, badges, tabs...)
static/manifest.json, static/sw.js → PWA (installable, app shell en cache)
scripts/seed_stripe.py   → script pour peupler ton compte Stripe test
```

Toutes les fonctions d'`analytics.py` prennent en entrée le même format de
donnée (`{"customer": str, "amount": float, "created": datetime, "country": str | None, "currency": str}`),
qu'elle vienne de vraies charges Stripe ou du générateur de démo — ce qui
permet de brancher les deux sources sur les mêmes analyses. Le filtre de
période (`analytics.filter_by_date`) et le filtre de devise s'appliquent en
amont, dans `main.get_records()`, donc chaque route bénéficie
automatiquement des paramètres `start`/`end`/`currency` sans logique
dupliquée.

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
| MRR (Monthly Recurring Revenue) | `stripe_metrics.compute_mrr` | Combien de revenu récurrent est engagé *maintenant*, indépendamment de l'historique ? |
| Choropleth géographique | `analytics.revenue_by_country` | Où sont mes clients, et où le CA se concentre-t-il ? |
| OAuth 2.0 (Stripe Connect) | `main.py` (`/auth/login`, `/auth/callback`) | Comment authentifier un utilisateur *et* accéder à ses données tierces sans jamais voir son mot de passe |

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

npx wrangler secret put STRIPE_SECRET_KEY
npx wrangler secret put STRIPE_CONNECT_CLIENT_ID
npx wrangler secret put SESSION_SECRET
npx wrangler secret put CF_ACCOUNT_ID
npx wrangler secret put CF_API_TOKEN
npx wrangler secret put CF_D1_DATABASE_ID

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
  cohérent avec un usage perso/quelques testeurs (pas de montée en charge
  prévue).
- Le cache mémoire des appels Stripe (`_cache` dans `main.py`, 60s) est
  perdu à chaque redémarrage du conteneur — normal, ce n'est qu'un cache.
- **Un `wrangler secret put` seul ne redémarre pas un conteneur déjà en
  cours d'exécution** si le code n'a pas changé (l'image Docker est
  identique, donc réutilisée) : le process garde l'ancienne valeur du
  secret en mémoire. Après avoir changé un secret, faire un petit
  changement de code (même un commentaire) avant `wrangler deploy` pour
  garantir un vrai rebuild/redémarrage.

## Pour aller plus loin

- **Connexion alternative (Google, etc.)** pour élargir l'accès au-delà des
  personnes ayant déjà un compte Stripe — supposerait de séparer
  l'authentification (qui est cette personne ?) de la connexion aux données
  (à quel compte Stripe a-t-elle accès ?), deux choses confondues
  aujourd'hui dans un seul bouton "Se connecter avec Stripe"
- **Webhooks temps réel** (`stripe listen` / endpoint Connect) plutôt que le
  cache 60s actuel — pertinent si le dashboard doit refléter les
  changements en quelques secondes plutôt qu'en une minute
- **Passer le scope OAuth de `read_write` à `read_only`** (demande à
  faire auprès du support Stripe) avant une ouverture à plus grande échelle

Une piste qui attend d'avoir plus de données réelles pour être utile :

- Remplacer la régression linéaire par une méthode plus robuste (moyenne
  mobile pondérée, décomposition saisonnière) — sur peu de mois d'historique,
  une méthode plus complexe n'est pas forcément plus fiable

## Stack

Python · FastAPI · Stripe API (Charges, Subscriptions, Connect OAuth) ·
Cloudflare D1 (comptes utilisateurs) · Chart.js (+ chartjs-chart-geo pour la
carte) — aucun framework frontend, aucune librairie de data science : tout
le calcul est fait à la main pour rester pédagogique. Déployé sur
Cloudflare Containers (voir plus haut).
