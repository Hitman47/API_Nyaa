# Plan d'implémentation et recette

Ce document ordonne le futur développement. Il ne constitue pas une
implémentation.

## Phase 1 — Socle et contrat

- structure Python/FastAPI ;
- settings validés ;
- modèles Pydantic ;
- enveloppe commune ;
- erreurs et `X-Request-Id` ;
- `/health`, docs, ReDoc et OpenAPI ;
- tests de contrat initiaux.

Critère de sortie : OpenAPI généré, routes déclarées, aucun accès réseau réel.

## Phase 2 — Source RSS strictement verrouillée

- QueryBuilder fermé ;
- mapping des paramètres ;
- fetcher/régulateur ;
- parser RSS sur fixtures ;
- validation `categoryId=3_1` ;
- `/latest` et `/search` ;
- génération magnet depuis l'info hash ;
- tests d'injection et de catégorie.

Critère de sortie : impossible d'émettre une requête vers une autre catégorie.

## Phase 3 — Détails

- parser de fiche HTML ;
- uploader, description assainie et liste de fichiers ;
- pagination des fichiers ;
- `/torrents/{id}` et `/torrents/by-hash/{hash}` ;
- vérification de catégorie du détail ;
- fixtures d'erreurs et changements HTML.

## Phase 4 — Classification et résolution

- normalisation des titres ;
- règles explicables et versionnées ;
- classification titre seul puis enrichie ;
- filtre `media_type` ;
- recherche secondaire unique ;
- ranking `/search/resolve` ;
- corpus de tests représentatif manga/LN/roman/artbook/magazine/unknown.

Critère de sortie : aucune suppression silencieuse d'un cas ambigu.

## Phase 5 — Cache et plafond disque

- LRU mémoire ;
- SQLite positif/négatif ;
- stale cache ;
- ETag/304 ;
- quota DB et global `/data` ;
- purge et maintenance ;
- WAL borné ;
- refus d'écriture sans perte de réponse ;
- métriques runtime.

Critère de sortie : les tests dépassant artificiellement 350 000 000 octets ne
permettent aucune nouvelle écriture et ne corrompent pas la base.

## Phase 6 — Sécurité et exploitation

- Bearer optionnel ;
- rate limiting client ;
- redirections upstream contrôlées ;
- logs sans secrets ;
- Dockerfile non-root ;
- Compose ZimaOS durci ;
- healthcheck ;
- limites `/tmp` et logs.

## Phase 7 — Documentation et distribution

- README final ;
- guide d'intégration et recettes ;
- exemples JSON ;
- validation docs/OpenAPI ;
- CI ;
- build multi-arch ;
- scan de vulnérabilités ;
- publication GHCR publique ;
- création du dépôt public `Hitman47/API_Nyaa`.

## Matrice de recette minimale

### Périmètre

- recherche sans paramètre de catégorie produit `c=3_1` ;
- tentative `category=1_2` rejetée comme paramètre inconnu ;
- doublon ou injection de `c` impossible ;
- item RSS `3_2` rejeté ;
- détail hors `3_1` renvoie `OUT_OF_SCOPE_RESOURCE` ;
- aucune réponse métier ne contient un item hors `3_1`.

### Recherche

- filtres `f=0`, `f=1`, `f=2` correctement mappés ;
- pagination et tri bornés ;
- `limit > 75` rejeté ;
- une recherche normale effectue au plus un fetch RSS à cache froid ;
- fallback effectue au plus un fetch additionnel ;
- seeders secondaires dans le ranking ;
- `best=null` lorsque le seuil n'est pas atteint.

### Classification

- CBZ favorise manga ;
- EPUB + `light novel` favorise light novel ;
- artbook/databook favorise artbook ;
- PDF seul reste ambigu ;
- magazine exclu de `media_type=all` ;
- unknown conservé ;
- signaux et confiance exposés.

### Détails

- HTML dangereux assaini ;
- `include_raw=false` par défaut ;
- fichiers paginés, maximum 1000 ;
- info hash validé sur 40 hexadécimaux ;
- aucun fichier `.torrent` écrit sur disque.

### Cache

- hit frais ;
- negative cache ;
- stale sur panne upstream ;
- 304 sur ETag ;
- invalidation par version de parser/classifieur ;
- purge à 90 % ;
- écriture refusée à la limite dure ;
- `/data` ne dépasse pas 350 000 000 octets après stabilisation/checkpoint ;
- la réponse upstream reste disponible si le cache ne peut plus écrire.

### Sécurité

- token vide = routes ouvertes ;
- token défini = routes métier protégées ;
- docs et `/health` accessibles ;
- comparaison du token résistante au timing ;
- 429 avec `Retry-After` ;
- redirection vers un hôte non autorisé refusée ;
- secrets, query brute et magnet complet absents des logs.

### Docker et CI

- conteneur non-root et filesystem en lecture seule hors mounts ;
- `container_name` exactement `API_Nyaa`, DNS Compose `api_nyaa` ;
- healthcheck fonctionnel ;
- port par défaut `49191` ;
- valeurs de plafond présentes et non relevables par `.env` ;
- logs rotatifs ;
- image amd64 et arm64 ;
- tests, OpenAPI, build et scan passent ;
- package GHCR public.

## Hors recette V1

- qualité réelle de toutes les classifications possibles ;
- disponibilité permanente de Nyaa ;
- téléchargement BitTorrent ;
- comportement qBittorrent ;
- exactitude linguistique du contenu d'un torrent correctement classé en `3_1`.
