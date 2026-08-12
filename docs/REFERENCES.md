# Références techniques étudiées

Ce document conserve les références ayant servi au cadrage. Il ne remplace pas
les tests contre le service réel au moment de l'implémentation.

## Projet de référence

- [Hitman47/APIManga_News](https://github.com/Hitman47/APIManga_News) : structure
  FastAPI, enveloppe JSON, cache SQLite, ETag, documentation et exploitation
  Docker ayant servi de modèle fonctionnel.

## Nyaa

- [Vue demandée : Literature → English-translated](https://nyaa.si/?q=&f=0&c=3_1)
- [Code source public de Nyaa](https://github.com/nyaadevs/nyaa)
- [Route officielle de recherche et RSS](https://github.com/nyaadevs/nyaa/blob/master/nyaa/views/main.py)
- [Template RSS officiel](https://github.com/nyaadevs/nyaa/blob/master/nyaa/templates/rss.xml)

Le code public confirme notamment :

- l'utilisation de `page=rss` ;
- les paramètres `q`, `c`, `f`, `u`, `p`, `s` et `o` ;
- l'option magnet ;
- les champs RSS `seeders`, `leechers`, `downloads`, `infoHash`, `categoryId`,
  `category`, `size`, `comments`, `trusted` et `remake` ;
- le fait que la résolution spéciale par info hash appartient au chemin HTML et
  nécessite donc une validation finale de la fiche.

## Ports

- [Registre IANA des ports et services](https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xhtml)

Le port initialement envisagé `3911` est enregistré pour un service d'état
d'imprimante. Le port par défaut retenu, `49191`, se situe dans la plage
dynamique/privée. Il reste configurable afin de gérer un conflit local sur
ZimaOS.

## Validation à refaire pendant l'implémentation

- réponse RSS réelle de `nyaa.si` pour `c=3_1` ;
- comportement réel des tris et de la pagination ;
- disponibilité simultanée du lien `.torrent` et des données nécessaires au
  magnet ;
- structure HTML actuelle des fiches, descriptions et listes de fichiers ;
- réponse en cas de hash absent ou appartenant à une autre catégorie ;
- headers de cache et de limitation éventuellement renvoyés par Nyaa.
