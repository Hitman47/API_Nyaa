# Cahier des charges — API_Nyaa

## 1. Objectif

Construire une API privée et auto-hébergeable qui expose une vue JSON stable de
Nyaa pour les mangas et livres traduits en anglais. Le service doit fonctionner
dans un conteneur Docker sur ZimaOS et reprendre la qualité documentaire et
opérationnelle de `APIManga_News`.

L'API sert de couche d'abstraction entre Nyaa et les applications clientes. Sa
première intégration cible sera `MangaFinder`, qui utilisera ensuite les liens
retournés pour piloter qBittorrent.

## 2. Périmètre obligatoire

### Inclus

- dernières publications de la catégorie `3_1` ;
- recherche textuelle ;
- tri et pagination ;
- filtres Nyaa `all`, `no_remakes` et `trusted` ;
- récupération d'une fiche détaillée ;
- recherche par identifiant et info hash ;
- résolution/ranking du meilleur candidat ;
- liens magnet et URL `.torrent` ;
- description nettoyée, uploader et liste paginée des fichiers sur les détails ;
- classification indicative du type de média ;
- cache, ETag, stale cache et cache négatif ;
- authentification Bearer optionnelle ;
- rate limiting client et régulation de l'upstream ;
- métriques runtime sans télémétrie externe ;
- Swagger, ReDoc et OpenAPI ;
- Docker Compose, CI, analyse de vulnérabilités et publication GHCR multi-arch.

### Exclus de la V1

- téléchargement ou stockage de fichiers `.torrent` ;
- téléchargement de contenu BitTorrent ;
- connexion ou identifiants qBittorrent ;
- ajout automatique d'un torrent à un client ;
- comptes utilisateurs ou interface d'administration ;
- catégories Nyaa configurables ;
- recherche par uploader ;
- filtre minimal de seeders ;
- vérification du contenu réel des fichiers pour certifier leur langue ;
- autre source ou agrégation multi-provider.

## 3. Règle de catégorie — invariant de sécurité métier

La catégorie Nyaa `3_1` est une constante interne, jamais un paramètre public.

L'invariant suivant doit être vérifié à plusieurs niveaux :

1. toute URL de recherche upstream contient `c=3_1` ;
2. le constructeur d'URL refuse toute tentative de remplacer la catégorie ;
3. chaque élément RSS est validé avec `categoryId == "3_1"` ;
4. une fiche détaillée hors catégorie est traitée comme hors périmètre ;
5. les tests vérifient qu'aucune valeur contrôlée par le client ne peut produire
   une autre catégorie ;
6. la catégorie peut être exposée dans la réponse pour audit, mais elle reste
   constante et non modifiable.

La catégorie `3_1` est considérée comme la preuve suffisante de la traduction
anglaise. L'API ne tente pas d'inspecter ou de télécharger les fichiers pour
revalider la langue.

## 4. Types de médias

Le service essaie de distinguer :

- `manga` ;
- `light_novel` ;
- `novel` ;
- `artbook` — inclut artbooks, databooks et guides ;
- `magazine` — détecté mais exclu du rendu par défaut ;
- `unknown`.

La classification est heuristique. Un résultat ambigu ne doit pas être supprimé
silencieusement : il reçoit `media_type=unknown` ou une confiance faible.

Le filtre public est :

```text
media_type=all|manga|light_novel|novel|artbook|unknown
```

`all` exclut les magazines mais conserve les inconnus. Un paramètre explicite
pour `magazine` n'est pas exposé en V1.

## 5. Contraintes de charge

Le service vise un usage personnel et occasionnel depuis une application :

- priorité au RSS structuré ;
- enrichissement HTML paresseux ;
- une requête upstream pour une recherche normale ;
- recherche secondaire seulement si aucun résultat exploitable n'est trouvé ;
- maximum deux requêtes Nyaa simultanées ;
- cadence globale moyenne maximale d'une requête Nyaa par seconde ;
- cache systématique des appels cacheables ;
- timeouts bornés et retries limités.

## 6. Contraintes de stockage

Le répertoire `/data` ne doit jamais dépasser `350 000 000` octets.

- objectif maximal du fichier SQLite principal : `256 000 000` octets ;
- WAL, SHM, diagnostics et autres fichiers de `/data` comptent dans la limite
  globale ;
- aucun fichier `.torrent` ni corps HTML normal n'est conservé ;
- diagnostics HTML désactivés par défaut ;
- purge préventive avant la limite ;
- si la purge échoue, les nouvelles écritures de cache sont refusées ;
- l'absence de cache ne doit pas empêcher une réponse upstream valide ;
- la taille de l'image Docker ne fait pas partie du quota `/data`.

Docker Compose transporte et verrouille les valeurs de plafond. La garantie
effective est appliquée par l'application, car un bind mount Docker générique ne
supporte pas de quota portable.

## 7. Sécurité et exposition

- déploiement derrière un VPN ;
- `API_TOKEN` vide : routes métier ouvertes ;
- `API_TOKEN` défini : Bearer obligatoire sur les routes métier ;
- `/health`, `/docs`, `/redoc` et `/openapi.json` restent accessibles sans
  token ;
- limitation à 60 requêtes/minute par identité client ;
- identité = token valide si présent, sinon IP ;
- pas de secrets dans les logs ;
- URLs upstream construites depuis des paramètres encodés, jamais concaténées
  librement ;
- `NYAA_BASE_URL` validée par allowlist en production ;
- HTML nettoyé avant exposition ;
- liens retournés, jamais suivis sur demande du client.

## 8. Déploiement et distribution

- dépôt : `Hitman47/API_Nyaa`, public ;
- branche par défaut : `main` ;
- licence : MIT ;
- image : `ghcr.io/hitman47/api_nyaa` publique ;
- plateformes : `linux/amd64`, `linux/arm64` ;
- tags : `latest`, version sémantique, SHA court ;
- nom du conteneur : `API_Nyaa` ;
- nom du service et DNS Compose : `api_nyaa` ;
- port interne : `8000` ;
- port ZimaOS par défaut : `49191` ;
- volume persistant : `./data:/data` ;
- redémarrage : `unless-stopped` ;
- healthcheck Docker obligatoire.

## 9. Documentation

La documentation narrative principale est en français. Les identifiants de
schémas, champs et paramètres sont en anglais.

Les livrables documentaires de la V1 comprennent :

- README d'installation rapide ;
- guide d'intégration route par route ;
- architecture ;
- exploitation et dépannage ;
- recettes `curl` ;
- exemples JSON versionnés ;
- OpenAPI validé automatiquement ;
- Swagger `/docs` ;
- ReDoc `/redoc` ;
- schéma brut `/openapi.json`.

## 10. Définition de terminé

La V1 est terminée lorsque :

- tous les critères de recette de `IMPLEMENTATION_PLAN.md` passent ;
- aucun test ne permet de sortir de `c=3_1` ;
- le plafond `/data` est testé à proximité et au-dessus de la limite ;
- les réponses et erreurs correspondent au contrat OpenAPI ;
- l'image démarre sur amd64 et arm64 ;
- la CI, le scan de vulnérabilités et la publication GHCR sont opérationnels ;
- un déploiement ZimaOS documenté répond sur le port `49191` ;
- aucune dépendance à qBittorrent n'existe dans `API_Nyaa`.
