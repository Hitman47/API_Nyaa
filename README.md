# API_Nyaa

API REST non officielle et auto-hébergeable pour les mangas et livres en anglais
de Nyaa, limitée de façon permanente à **Literature → English-translated**
(`c=3_1`). Elle expose un JSON stable, les liens magnet et les URL `.torrent`.
Elle ne télécharge aucun contenu et ne contient aucune intégration qBittorrent.

> Ce projet n'est ni affilié à Nyaa ni approuvé par Nyaa. Respectez les lois,
> licences et conditions d'utilisation applicables aux contenus consultés.

## Démarrage sur ZimaOS / Docker Compose

Le port hôte retenu est `49191` et le conteneur s'appelle `API_Nyaa`.

```bash
docker compose pull
docker compose up -d
curl http://ADRESSE_DU_ZIMAOS:49191/health
```

Pour protéger les routes métier, créez un fichier `.env` à côté du Compose :

```dotenv
API_TOKEN=une-longue-valeur-secrete
```

Puis envoyez `Authorization: Bearer une-longue-valeur-secrete`. `/health`,
`/docs`, `/redoc` et `/openapi.json` restent publics. Il est recommandé de
conserver l'API derrière le VPN prévu.

Le volume Docker `api_nyaa_data` conserve uniquement le cache SQLite. Les
plafonds inscrits directement dans le Compose réservent au maximum
`350 000 000` octets à `/data`, dont 256 Mo pour la base et 32 Mo pour le WAL.
L'image du conteneur et les logs Docker rotatifs sont hors de ce budget.

## Routes

| Route | Rôle |
| --- | --- |
| `GET /health` | santé du processus |
| `GET /health/runtime` | cache, quotas, limiteurs et métriques agrégées |
| `GET /latest` | dernières publications de `c=3_1` |
| `GET /search?q=...` | recherche filtrée et triée |
| `GET /search/resolve?q=...` | meilleur candidat et classement |
| `GET /torrents/{id}` | fiche, description et fichiers |
| `GET /torrents/by-hash/{hash}` | résolution d'un info hash |

Exemple :

```bash
curl "http://localhost:49191/search?q=ascendance+of+a+bookworm&media_type=light_novel&limit=10"
```

Les paramètres `category`, `c`, `uploader` et leurs alias sont rejetés. Chaque
élément retourné est revalidé avec `category_id=3_1`. Les magazines détectés
sont exclus par défaut ; les éléments ambigus restent disponibles sous le type
`unknown`. Les réponses métier utilisent des ETag et peuvent servir un cache
stale si Nyaa est momentanément indisponible.

## Développement

Python 3.12 est requis.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python run_server.py
```

Sous Windows, utilisez `.venv\Scripts\python.exe`. Le serveur écoute alors sur
`http://localhost:8000`. Les tests reposent sur des fixtures locales et ne
sollicitent pas Nyaa.

## Documentation

- Swagger UI : `/docs`
- ReDoc : `/redoc`
- OpenAPI vivant : `/openapi.json`
- [OpenAPI versionné](docs/openapi.json)
- [Contrat HTTP](docs/API_CONTRACT.md)
- [Architecture et classification](docs/ARCHITECTURE.md)
- [Déploiement et exploitation](docs/OPERATIONS.md)
- [Cahier des charges](docs/SPECIFICATION.md)
- [Plan et critères de recette](docs/IMPLEMENTATION_PLAN.md)
- [Références techniques](docs/REFERENCES.md)

La CI vérifie Ruff, les tests, la couverture, OpenAPI, le Compose, le build et
le scan Trivy. Les tags `v*` publient l'image publique
`ghcr.io/hitman47/api_nyaa` pour `linux/amd64` et `linux/arm64`, avec SBOM et
provenance. Licence [MIT](LICENSE).
