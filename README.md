# 📊 metrics-dash

Dashboard de métriques e-commerce branché sur Stripe et Shopify, construit
pour apprendre les techniques d'analyse de données ("BI") qu'on retrouve
dans des outils comme Power BI ou Tableau — implémentées ici à la main en
Python, sans librairie de data science, pour que la logique reste lisible.

L'accès passe par **Google** (identité) puis, une fois connecté, chacun
relie **ses propres sources de données** (Stripe et/ou Shopify) : le
dashboard n'affiche jamais que ses données à lui, en temps quasi réel, deux
canaux de vente pouvant s'additionner dans le même CA analysé. Voir
[Connexion & comptes](#connexion--comptes) ci-dessous pour le détail du flux.

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
- **Connexion Google** — identifie la personne, indépendamment de ses
  connexions Stripe/Shopify (voir [Connexion & comptes](#connexion--comptes))
- **Shopify** — deuxième canal de vente possible en plus de Stripe ; ses
  commandes rejoignent les charges Stripe dans le même chiffre d'affaires
  analysé, sans jamais mélanger les devises entre elles
- **Profil du compte** — popup (clic sur "Bonjour {nom}" dans le header)
  récapitulant l'identité, la date d'inscription, le secteur, et l'état de
  chaque connexion avec un bouton de déconnexion indépendant pour chacune
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

L'architecture sépare volontairement deux responsabilités distinctes :
**l'identité** (qui es-tu ?) et **l'accès aux données** (à quelles données
as-tu droit ?) — deux questions différentes, deux mécanismes différents.

### 1. Identité : connexion Google (obligatoire)

Rien n'est accessible — pas même le mode démo — sans se connecter d'abord
avec Google (`/auth/google/login`, flux OAuth 2.0 standard "userinfo
endpoint"). Cette étape ne fait que savoir qui utilise le dashboard ; elle
n'implique aucune donnée métier. Une identité minimale (`users` dans D1)
est créée ou retrouvée à cette occasion.

### 2. Accès aux données : Stripe et/ou Shopify (indépendants)

Une fois identifié, deux sources de vente peuvent être connectées, l'une
sans dépendre de l'autre :

- **Stripe** (`/auth/login`) — flux OAuth de
  [Stripe Connect](https://stripe.com/docs/connect) : redirection vers
  l'écran d'autorisation standard de Stripe, la personne se connecte avec
  **son propre compte Stripe** et autorise l'accès ; le jeton obtenu est
  stocké dans `stripe_connections` (D1), lié à l'utilisateur Google courant.
- **Shopify** (`/auth/shopify/login?shop=xxx.myshopify.com`) — flux OAuth
  d'une [app personnalisée Shopify](https://shopify.dev/docs/apps/build/scaffold-app)
  créée sur le Dev Dashboard, scope `read_orders` **déclaré en portée
  requise** (`Portées`, pas `Portées facultatives` — les portées
  facultatives suivent un mécanisme d'autorisation différent, non
  implémenté ici) ; nécessite aussi d'avoir activé l'accès aux **données
  clients protégées** (`Protected customer data`) côté configuration de
  l'app, sans quoi l'API renvoie un `403 Forbidden` malgré un jeton valide.

Chaque connexion peut être coupée indépendamment (`/auth/stripe/disconnect`,
`/auth/shopify/disconnect`, boutons "Déconnecter" à côté de chaque pastille
de statut) sans perdre l'identité Google ni l'autre connexion. Les données
des deux sources connectées **s'additionnent** dans le même chiffre
d'affaires analysé (`main.get_all_records()`) — deux canaux de vente, un
seul dashboard — et une source en échec (jeton révoqué, boutique
injoignable) n'empêche jamais l'affichage de l'autre.

**À savoir (Stripe)** : le scope demandé est `read_write` (pas
`read_only`) — Stripe réserve `read_only` aux plateformes validées
spécifiquement sur demande. Le code n'appelle que des endpoints Stripe en
lecture (`Charge.list`, `Subscription.list`, `Account.retrieve`), jamais
d'écriture, mais le jeton obtenu le permettrait techniquement. Compromis
raisonnable pour un usage entre quelques utilisateurs de confiance ; à
reconsidérer avant une ouverture plus large.

**Parcours historique** : le bouton Stripe fonctionnait initialement seul,
sans Google — une identité minimale était créée à la volée. Ce chemin
existe encore côté backend (`db.upsert_standalone_stripe_user`) mais n'est
plus accessible depuis l'interface : Google est désormais le préalable
obligatoire à toute connexion de données.

### Mode démo

Ne nécessite aucune connexion de données, mais reste **derrière la
connexion Google** comme le reste du dashboard : un jeu de données simulées
est généré à la volée (`demo_data.py`).

### RGPD, en bref

Se connecter avec Google (ou Stripe) collecte des données personnelles
(nom, email pour Google ; nom de compte pour Stripe) — dans les deux cas,
c'est le fournisseur OAuth (Google ou Stripe) qui affiche son propre écran
de consentement avant de transmettre quoi que ce soit à ce serveur. Tant
que ce projet reste un usage personnel/test, l'exposition légale est
faible ; une politique de confidentialité deviendrait nécessaire avant
d'ouvrir le service à de vrais utilisateurs externes.

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
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` : identifiants OAuth d'un
  client "Web application" créé sur https://console.cloud.google.com
  (APIs & Services → Identifiants) — nécessaires pour tester la connexion
  Google en local ; l'URI de redirection autorisée doit correspondre
  exactement à `<ton domaine>/auth/google/callback`
- `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET` : identifiants d'une app
  personnalisée créée sur https://dev.shopify.com/dashboard, avec le scope
  `read_orders` en **portée requise** (`Portées`, pas `Portées
  facultatives`) et l'accès aux données clients protégées activé (sans
  quoi l'API renvoie un `403 Forbidden`) — voir
  [Connexion & comptes](#connexion--comptes)
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
main.py                  → routes FastAPI, auth Google/Stripe/Shopify, cache mémoire (60s), export CSV
auth.py                  → jetons de session signés (HMAC), stateless (pas de table sessions)
db.py                    → client Cloudflare D1 via son API REST (users, stripe_connections, shopify_connections)
stripe_metrics.py        → appels à l'API Stripe (charges, abonnements) + normalisation en records
shopify_metrics.py       → appels à l'API Admin Shopify (commandes) + normalisation au même format
analytics.py             → techniques "BI" : croissance, RFM, cohortes, prévision, géo, fidélité
demo_data.py             → générateur de données simulées pour le mode démo
static/index.html        → frontend HTML/JS/Chart.js, sans framework ni build step
static/design-system.css → variables CSS, palette dark/light, composants (cards, badges, tabs...)
static/manifest.json, static/sw.js → PWA (installable, app shell en cache, network-first)
scripts/seed_stripe.py   → script pour peupler ton compte Stripe test
```

Base de données (D1) : `users` porte l'identité (Google), tandis que
`stripe_connections` et `shopify_connections` portent chacune l'accès à
une source de données, chacune liée à `users.id` — un utilisateur peut
exister sans aucune connexion de données (mode démo), avec l'une, l'autre,
ou les deux à la fois.

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
| OAuth 2.0 (Stripe Connect, Shopify) | `main.py` (`/auth/login`, `/auth/shopify/login`) | Comment accéder aux données d'un compte tiers sans jamais voir son mot de passe |
| OAuth 2.0 "userinfo endpoint" (Google) | `main.py` (`/auth/google/login`) | Comment identifier une personne indépendamment de tout accès à ses données métier |
| Séparation authentification / autorisation | `db.py` (`users` vs `stripe_connections`/`shopify_connections`) | Comment permettre à quelqu'un de s'identifier sans forcément relier de données, et inversement |

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
npx wrangler secret put GOOGLE_CLIENT_ID
npx wrangler secret put GOOGLE_CLIENT_SECRET
npx wrangler secret put SHOPIFY_API_KEY
npx wrangler secret put SHOPIFY_API_SECRET
npx wrangler secret put CF_ACCOUNT_ID
npx wrangler secret put CF_API_TOKEN
npx wrangler secret put CF_D1_DATABASE_ID

npx wrangler deploy
```

**Important** : chaque secret doit aussi être relayé explicitement dans
`src/index.ts` (l'interface `Env` + l'objet `envVars` du Worker) — un
`wrangler secret put` seul ne suffit pas, le Worker ne transmet au
container que les variables qu'il liste nommément. Un secret ajouté ici
sans son entrée dans `src/index.ts` reste invisible du code Python.

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
  garantir un vrai rebuild/redémarrage. Ça vaut aussi après avoir ajouté un
  secret dans `src/index.ts` : ce fichier fait partie du Worker, pas de
  l'image Docker, donc son changement seul ne déclenche pas de rebuild —
  le Worker est bien redéployé (nouveau "Version ID"), mais le container
  tournant garde ses anciennes `envVars` tant qu'il n'a pas lui-même
  redémarré.
- **Le service worker (`static/sw.js`) doit servir l'app shell en
  network-first, jamais cache-first** : une fois un fichier mis en cache,
  un navigateur ne revérifie plus jamais le réseau spontanément — seul un
  rechargement forcé (Cmd/Ctrl+Shift+R) contourne le service worker.
  Incrémenter `CACHE_NAME` (ex: `v2` → `v3`) purge le cache existant chez
  les visiteurs qui l'avaient déjà, utile après un changement structurel
  du HTML.
- **Une contrainte `UNIQUE` sur deux colonnes différentes d'une même table**
  (ex: `stripe_connections.user_id` et `stripe_connections.stripe_user_id`)
  nécessite une clause `ON CONFLICT` par colonne dans l'upsert SQLite/D1 —
  n'en gérer qu'une fait échouer silencieusement l'écriture dès que
  l'autre contrainte est violée (ex: reconnecter un compte Stripe déjà
  lié à un ancien utilisateur).

## Pour aller plus loin

- **Politique de confidentialité + CGU** avant toute ouverture à de vrais
  utilisateurs externes (voir [RGPD, en bref](#rgpd-en-bref))
- **Webhooks temps réel** (`stripe listen` / endpoint Connect) plutôt que le
  cache 60s actuel — pertinent si le dashboard doit refléter les
  changements en quelques secondes plutôt qu'en une minute
- **Passer le scope OAuth de `read_write` à `read_only`** (demande à
  faire auprès du support Stripe) avant une ouverture à plus grande échelle
- **D'autres sources de données** (WooCommerce, Google Ads/Meta Ads pour le
  ROAS, email marketing, analytics web) — même principe que Shopify :
  normaliser vers le format `{customer, amount, created, country, currency}`
  partagé par `analytics.py`

Une piste qui attend d'avoir plus de données réelles pour être utile :

- Remplacer la régression linéaire par une méthode plus robuste (moyenne
  mobile pondérée, décomposition saisonnière) — sur peu de mois d'historique,
  une méthode plus complexe n'est pas forcément plus fiable

## Stack

Python · FastAPI · Stripe API (Charges, Subscriptions, Connect OAuth) ·
Shopify Admin API (Orders, app OAuth) · Google OAuth 2.0 (identité) ·
Cloudflare D1 (comptes utilisateurs) · Chart.js (+ chartjs-chart-geo pour la
carte) — aucun framework frontend, aucune librairie de data science : tout
le calcul est fait à la main pour rester pédagogique. Déployé sur
Cloudflare Containers (voir plus haut).
