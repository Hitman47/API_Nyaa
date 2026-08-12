# Déploiement et exploitation

## 1. Cible de production

- ZimaOS avec Docker Compose ;
- conteneur `API_Nyaa` ;
- service et DNS Compose `api_nyaa` ;
- image publique `ghcr.io/hitman47/api_nyaa` ;
- port interne `8000` ;
- port hôte par défaut `49191` ;
- volume persistant nommé `api_nyaa_data:/data` ;
- architectures amd64 et arm64.

Le port hôte est volontairement fixé à `49191` dans le Compose de référence.

## 2. Contrat Docker Compose

Le Compose contient :

- image GHCR versionnée ;
- `container_name: API_Nyaa` et service `api_nyaa` ;
- mapping `49191:8000` ;
- volume nommé `/data` ;
- `restart: unless-stopped` ;
- healthcheck sur `/health` ;
- `init: true` ;
- `read_only: true` pour le filesystem du conteneur ;
- `tmpfs` pour `/tmp`, taille maximale 16 Mo ;
- `security_opt: no-new-privileges:true` ;
- suppression des capabilities Linux inutiles ;
- rotation de logs Docker, 10 Mo × 3 fichiers ;
- un seul worker Uvicorn ;
- plafonds de données fixés explicitement.

Les deux valeurs suivantes ne doivent pas dépendre d'une variable hôte en
production :

```text
DATA_HARD_LIMIT_BYTES=350000000
CACHE_DB_TARGET_BYTES=256000000
SQLITE_WAL_HARD_LIMIT_BYTES=32000000
MAX_CACHE_ENTRY_BYTES=5000000
```

Cela empêche une configuration accidentelle de relever le plafond. Une personne
qui souhaite modifier ces limites devra éditer explicitement le Compose.

## 3. Variables d'environnement

### Application

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `APP_NAME` | `API_Nyaa` | nom OpenAPI |
| `APP_ENV` | `production` | environnement |
| `LOG_LEVEL` | `INFO` | niveau de log |
| `LOG_FORMAT` | `text` | `text` ou `json` |
| `ENABLE_DOCS` | `true` | Swagger/ReDoc/OpenAPI |
| `API_TOKEN` | vide | Bearer optionnel |

### Nyaa

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `NYAA_BASE_URL` | `https://nyaa.si` | upstream validé |
| `NYAA_CATEGORY_ID` | `3_1` | constante, non surchargeable en production |
| `USER_AGENT` | `API_Nyaa/1.0 (+private-selfhosted)` | identification |
| `REQUEST_TIMEOUT_SECONDS` | `20` | timeout global |
| `REQUEST_MAX_RETRIES` | `2` | retries temporaires |
| `UPSTREAM_REQUESTS_PER_SECOND` | `1` | cadence |
| `UPSTREAM_MAX_CONCURRENCY` | `2` | concurrence |

### Cache et stockage

| Variable | Valeur production | Rôle |
| --- | ---: | --- |
| `DB_PATH` | `/data/cache.sqlite3` | SQLite |
| `DATA_HARD_LIMIT_BYTES` | `350000000` | plafond absolu `/data` |
| `CACHE_DB_TARGET_BYTES` | `256000000` | cible DB |
| `SQLITE_WAL_HARD_LIMIT_BYTES` | `32000000` | plafond applicatif WAL |
| `MAX_CACHE_ENTRY_BYTES` | `5000000` | entrée de cache maximale |
| `DEBUG_CAPTURE_HTML_ON_ERROR` | `false` | dumps HTML |
| `DEBUG_MAX_BYTES` | `50000000` | inclus dans le plafond global |
| `CACHE_TTL_SEARCH_SECONDS` | `300` | listes/recherches |
| `CACHE_TTL_DETAIL_SECONDS` | `21600` | fiches |
| `CACHE_STALE_GRACE_SECONDS` | `604800` | 7 jours |
| `NEGATIVE_CACHE_TTL_SECONDS` | `120` | négatif |

### Limitation client

| Variable | Défaut | Rôle |
| --- | ---: | --- |
| `RATE_LIMIT_ENABLED` | `true` | toujours actif en production |
| `RATE_LIMIT_REQUESTS` | `60` | quota |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | fenêtre |
| `RATE_LIMIT_SCOPE` | `ip_or_token` | identité |

## 4. Quota disque : garanties et limites

Un bind mount Docker n'a pas de quota portable dans Compose. La stratégie est
donc composée de protections complémentaires :

1. valeurs verrouillées dans le Compose ;
2. contrôle applicatif de tous les fichiers `/data` ;
3. limite SQLite du fichier principal ;
4. WAL plafonné à 32 Mo et checkpointé ;
5. diagnostics inclus dans le même budget ;
6. arrêt des écritures de cache avant dépassement ;
7. `/tmp` sur tmpfs borné ;
8. logs Docker rotatifs et extérieurs à `/data`.

Le health runtime doit exposer : taille actuelle, plafond, pourcentage, taille
DB/WAL, dernière purge et état `writes_enabled`.

Le service doit refuser de démarrer si :

- le plafond configuré est supérieur à 350 000 000 en production ;
- la cible DB est supérieure au plafond global ;
- le budget DB + WAL + diagnostics ne laisse pas la réserve de sécurité requise ;
- `/data` n'est pas inscriptible ;
- la catégorie configurée n'est pas exactement `3_1`.

## 5. Mise à jour et rollback

Les images utilisent des tags immuables de version et SHA. `latest` est un alias
pratique, pas une stratégie de rollback.

Procédure recommandée :

1. noter le tag courant ;
2. sauvegarder uniquement `cache.sqlite3` si nécessaire — le cache peut aussi
   être supprimé sans perte métier ;
3. tirer le nouveau tag ;
4. recréer le conteneur ;
5. vérifier `/health`, `/health/runtime` et une recherche ;
6. revenir au tag précédent si le parseur upstream échoue.

Les versions de clés de cache empêchent de réutiliser des payloads incompatibles
après une évolution de parser ou de classifieur.

## 6. CI GitHub Actions

### Pull requests et pushes

- installation Python 3.12 ;
- lint/format check ;
- type checking si retenu par l'implémentation ;
- génération et validation OpenAPI ;
- validation des liens et exemples documentaires ;
- tests unitaires et de contrat ;
- tests de sécurité de la construction d'URL ;
- tests de plafond disque ;
- build Docker sans publication ;
- scan de vulnérabilités de l'image.

### Branche `main` et tags

- build multi-architecture avec Buildx ;
- publication GHCR pour amd64/arm64 ;
- attestations de provenance et SBOM si disponibles ;
- tags `latest`, `vX.Y.Z`, `X.Y`, `sha-<court>` ;
- scan de vulnérabilités après publication.

Le package GHCR doit être rendu public. Comme la première publication peut être
privée par défaut selon les réglages GitHub, cette visibilité doit être vérifiée
explicitement lors de la création du package.

## 7. Healthcheck

Le healthcheck Docker teste seulement `/health` avec un timeout court. Il ne doit
pas interroger Nyaa à chaque passage.

L'état upstream se trouve dans `/health/runtime` avec :

- dernier succès ;
- dernière erreur agrégée ;
- état du circuit/régulateur ;
- latence récente ;
- âge du dernier cache valide.

## 8. Dépannage attendu

### `UPSTREAM_FETCH_ERROR`

Vérifier DNS/VPN, disponibilité Nyaa, `Retry-After` et état du cache stale.

### `UPSTREAM_PARSE_ERROR`

Comparer une fixture récente au parser. Activer temporairement les diagnostics
uniquement si l'espace `/data` le permet.

### `CACHE_WRITE_SKIPPED`

Consulter `/health/runtime`, forcer une maintenance sûre et vérifier la taille du
WAL. Ne jamais supprimer récursivement `/data` depuis l'application.

### 429

Le client doit respecter `Retry-After`. MangaFinder devra réutiliser le cache et
éviter les recherches identiques rapprochées.

## 9. Intégration future MangaFinder

`MangaFinder` devra :

1. appeler `/search` ou `/search/resolve` ;
2. présenter le candidat et ses indicateurs ;
3. récupérer `magnet_url` ou `torrent_url` ;
4. envoyer le choix à son propre service qBittorrent ;
5. conserver les identifiants qBittorrent hors de `API_Nyaa`.
