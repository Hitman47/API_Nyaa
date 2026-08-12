# Contrat HTTP — API_Nyaa

## 1. Principes

- aucun préfixe `/v1` en V1, comme `APIManga_News` ;
- JSON UTF-8 ;
- dates au format ISO 8601 UTC ;
- champs optionnels explicitement `null` plutôt qu'inventés ;
- paramètres inconnus rejetés lorsqu'ils pourraient élargir le périmètre ;
- `category` et `uploader` ne sont pas des paramètres publics ;
- pagination basée sur `page`, avec `limit` appliqué localement ;
- `limit` par défaut `25`, minimum `1`, maximum `75`.

## 2. Enveloppe commune

Toutes les routes métier utilisent :

```json
{
  "schema_version": "1.0",
  "ok": true,
  "found": true,
  "source": "nyaa",
  "source_url": "https://nyaa.si/?page=rss&c=3_1...",
  "cached": false,
  "fetched_at": "2026-08-12T12:00:00Z",
  "cache_expires_at": "2026-08-12T12:05:00Z",
  "partial": false,
  "warnings": [],
  "fingerprint": "sha256:...",
  "data": {}
}
```

Sémantique :

- `found=false` : appel valide sans résultat exploitable ;
- `cached=true` : réponse servie depuis le cache ;
- `partial=true` : ancienne réponse servie après échec upstream ;
- `warnings` : avertissements structurés et non secrets ;
- `fingerprint` : empreinte du contenu métier, indépendante des dates de cache.

Lorsque `fingerprint` existe :

```http
ETag: "<fingerprint>"
X-Data-Fingerprint: <fingerprint>
```

Un `If-None-Match` identique produit `304 Not Modified` sans corps.

Toutes les réponses incluent `X-Request-Id`. Un identifiant client valide est
réutilisé ; sinon l'API en génère un.

## 3. Modèle TorrentSummary

```json
{
  "id": 1234567,
  "title": "Example Series Vol. 01 [Digital] [English]",
  "details_url": "https://nyaa.si/view/1234567",
  "published_at": "2026-08-12T11:42:00Z",
  "size": "312.4 MiB",
  "size_bytes": 327575142,
  "seeders": 12,
  "leechers": 1,
  "downloads": 85,
  "comments": 0,
  "info_hash": "0123456789ABCDEF0123456789ABCDEF01234567",
  "magnet_url": "magnet:?xt=urn:btih:...",
  "torrent_url": "https://nyaa.si/download/1234567.torrent",
  "trusted": true,
  "remake": false,
  "category_id": "3_1",
  "category_name": "Literature - English-translated",
  "media_type": "manga",
  "media_type_confidence": 0.87,
  "classification_signals": ["title_volume_marker", "detail_cbz_files"]
}
```

Règles :

- `category_id` vaut toujours `3_1` ;
- `magnet_url` peut être construit depuis l'info hash et les trackers configurés ;
- `torrent_url` est une URL, jamais un fichier proxifié ou stocké ;
- les compteurs sont des instantanés et peuvent être périmés ;
- `classification_signals` ne contient aucune donnée brute sensible.

## 4. Routes

### `GET /health`

Sans authentification.

```json
{ "ok": true }
```

Ce endpoint confirme le fonctionnement du processus, pas la disponibilité de
Nyaa.

### `GET /health/runtime`

Expose uniquement des agrégats : cache, quota, requêtes, erreurs, timeouts,
latences et état du régulateur upstream. Aucun token, URL de requête complète ou
titre recherché ne doit apparaître.

Champs minimaux :

- `ok` ;
- `uptime_seconds` ;
- `cache` ;
- `storage` ;
- `rate_limit` ;
- `upstream` ;
- `metrics` ;
- `defaults`.

### `GET /latest`

Paramètres :

- `page=1` ;
- `limit=25` ;
- `filter=all|no_remakes|trusted` ;
- `media_type=all|manga|light_novel|novel|artbook|unknown` ;
- `sort=date|seeders|leechers|downloads|size|comments` ;
- `order=asc|desc`.

Défauts : `filter=all`, `media_type=all`, `sort=date`, `order=desc`.

Réponse `data` :

```json
{
  "page": 1,
  "limit": 25,
  "has_more": true,
  "filter": "all",
  "media_type": "all",
  "sort": "date",
  "order": "desc",
  "items": []
}
```

### `GET /search`

Paramètres : ceux de `/latest`, plus :

- `q` obligatoire, 1 à 200 caractères ;
- `include_details=false`.

`include_details=true` autorise l'enrichissement d'un nombre borné de candidats
et ne peut jamais dépasser 10 fiches par requête.

Une recherche normale produit une seule requête RSS. Si aucun candidat utile
n'est trouvé, le service peut lancer une unique recherche secondaire avec un
indice adapté au `media_type` demandé. Cette décision apparaît dans `warnings`
et les métriques.

Réponse `data` :

```json
{
  "query": "example series",
  "page": 1,
  "limit": 25,
  "has_more": false,
  "filter": "all",
  "media_type": "manga",
  "sort": "date",
  "order": "desc",
  "items": []
}
```

### `GET /search/resolve`

Paramètres :

- `q` obligatoire ;
- `filter=all|no_remakes|trusted` ;
- `media_type=all|manga|light_novel|novel|artbook|unknown` ;
- `limit=10`, maximum `25` ;
- `include_details=true` par défaut, maximum 10 enrichissements.

Réponse `data` :

```json
{
  "query": "example series volume 3",
  "media_type_requested": "manga",
  "confidence": "high",
  "best": {},
  "candidates": [],
  "ranking_version": "1.0"
}
```

`best` peut être `null`. La confiance vaut `high`, `medium`, `low` ou `none`.

### `GET /torrents/{torrent_id}`

Paramètres :

- `include_description=true` ;
- `include_raw=false` ;
- `include_files=true` ;
- `files_offset=0` ;
- `files_limit=200`, maximum `1000`.

Réponse : `TorrentDetail`, qui étend `TorrentSummary` avec :

- `uploader` ;
- `information_url` ;
- `description_text` ;
- `description_html` uniquement si `include_raw=true`, après assainissement ;
- `files.total` ;
- `files.offset` ;
- `files.limit` ;
- `files.has_more` ;
- `files.items[].path` et `files.items[].size_bytes`.

Une fiche dont la catégorie n'est pas `3_1` produit `404 OUT_OF_SCOPE_RESOURCE`.

### `GET /torrents/by-hash/{info_hash}`

Accepte exactement 40 caractères hexadécimaux, insensibles à la casse. La
résolution peut utiliser la recherche HTML Nyaa, car la recherche spéciale par
hash n'est pas garantie dans le flux RSS. L'URL de départ contient malgré tout
`c=3_1` et la fiche atteinte après une éventuelle redirection est obligatoirement
revalidée. Une redirection vers un torrent hors `3_1` produit
`OUT_OF_SCOPE_RESOURCE`. La réponse finale reprend le même détail que la route
par identifiant.

## 5. Mapping des filtres Nyaa

| API | Paramètre Nyaa |
| --- | --- |
| `all` | `f=0` |
| `no_remakes` | `f=1` |
| `trusted` | `f=2` |

Le tri public est traduit vers les clés upstream par une table fermée. Aucune
clé arbitraire fournie par le client n'est transmise.

## 6. Ranking de `/search/resolve`

Score borné entre 0 et 100 :

- similarité et tokens du titre : jusqu'à 55 points ;
- correspondance exacte de phrase : jusqu'à 15 points ;
- type demandé : +10, type contradictoire : -15, inconnu : 0 ;
- trusted : +8 ;
- remake : -8 ;
- santé par seeders, logarithmique : jusqu'à 5 points ;
- récence : jusqu'à 4 points ;
- confiance de classification enrichie : jusqu'à 3 points.

Les seeders restent secondaires. Un livre rare très pertinent ne doit pas être
éliminé uniquement parce qu'il a peu de seeders.

Confiance proposée :

- `high` : score ≥ 80 et marge ≥ 10 sur le deuxième ;
- `medium` : score ≥ 65 ;
- `low` : score ≥ 50 ;
- `none` : aucun candidat ou score < 50.

Les seuils et poids sont versionnés et couverts par des fixtures de test.

## 7. Authentification

Lorsque `API_TOKEN` est défini :

```http
Authorization: Bearer <token>
```

Routes exemptées :

- `/health` ;
- `/docs` ;
- `/redoc` ;
- `/openapi.json`.

`/health/runtime` est une route métier et suit la politique du token.

## 8. Erreurs

Format normalisé :

```json
{
  "code": "UPSTREAM_FETCH_ERROR",
  "detail": "Nyaa is temporarily unavailable.",
  "request_id": "..."
}
```

| HTTP | Code | Cas |
| --- | --- | --- |
| 400 | `INVALID_QUERY` | requête vide ou inexploitable |
| 401 | `AUTH_REQUIRED` | Bearer absent ou invalide |
| 404 | `RESOURCE_NOT_FOUND` | identifiant/hash absent |
| 404 | `OUT_OF_SCOPE_RESOURCE` | ressource hors `3_1` |
| 422 | `INVALID_PARAMETER` | valeur hors contrat |
| 429 | `RATE_LIMITED` | quota client dépassé |
| 502 | `UPSTREAM_FETCH_ERROR` | erreur réseau/HTTP Nyaa |
| 502 | `UPSTREAM_PARSE_ERROR` | RSS/HTML inexploitable |
| 503 | `STORAGE_LIMIT_REACHED` | uniquement si une opération exige une écriture persistante |

Un échec d'écriture de cache ne transforme pas une lecture upstream réussie en
503 : la réponse est servie avec un warning `CACHE_WRITE_SKIPPED`.

## 9. Rate limiting

- 60 requêtes/minute par IP ou token ;
- fenêtre glissante ou token bucket ;
- headers `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` ;
- `Retry-After` sur 429 ;
- `/health` exempté ;
- docs exemptées ;
- limite upstream indépendante : cadence 1 req/s, concurrence 2.
