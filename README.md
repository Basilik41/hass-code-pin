# Codes d'accès (Home Assistant)

Code personnel pour les actions sensibles : portes, serrures, alarmes, surveillance, détections des caméras…

- **Code par personne**, vérifié par Home Assistant (jamais par la carte), stocké haché (PBKDF2-SHA256).
- **Tablettes murales** : comptes dont le nom contient « TabDomo » (réglable) → on choisit sa photo puis on tape son code. Le journal indique « Julie (tablette Salon) ».
- **Blocage** : 5 codes faux → code bloqué 5 min (puis 10, 20, 40, 1 h) ; 10 codes faux sur un même appareil en 15 min → appareil bloqué 15 min. Alerte des administrateurs.
- **Entités protégées** : celles qui portent l'étiquette « Protégé par code » (créée à l'installation).
- **Commandes directes** (tuile classique, API, script lancé par un utilisateur) sur une entité protégée : signalées en mode *observation*, annulées et signalées en mode *blocage* (porte refermée, serrure reverrouillée, alarme réarmée, interrupteur remis). Les automatisations ne sont jamais contrôlées.
- **Deux Home Assistant** : codes et blocages synchronisés automatiquement par les liaisons *Remote Home Assistant* existantes (ou une adresse + jeton administrateur dans les options). Une action sur une entité vue par la liaison (ex. `cover.grange_porte_ecurie` à la Maison) est exécutée par l'autre Home Assistant, code vérifié.

## Carte

```yaml
type: custom:code-pin-card
title: Accès Grange
entities:
  - cover.porte_ecurie
  - entity: input_boolean.vigilance_active
    name: Surveillance
mon_code: true      # « Définir / changer mon code »
gestion: false      # administrateurs : codes de la famille, synchronisation
journal: false      # administrateurs : 30 derniers jours
```

## Limites

- Home Assistant n'a pas de droits par entité : une commande directe ne peut pas être empêchée, seulement annulée aussitôt (≈ 2 s) et signalée.
- Une automatisation lancée à la main (`automation.trigger`) et les commandes HomeKit / Matter (sans compte) ne sont pas contrôlées. Pour empêcher qu'on désactive une automatisation de sécurité, lui mettre l'étiquette.
- Le compte du jeton de liaison (« Liaison ») est accepté pour la synchronisation sans être administrateur (option « Comptes de liaison ») ; ses commandes directes sont signalées « (ou via l'autre Home Assistant) ».
- Les codes de 4 chiffres résistent mal à un attaquant ayant accès aux fichiers ou aux sauvegardes : préférer 6 chiffres.

## Tests

`tests/test_pin.py` (fonctions pures), `tests/bench_code_pin.py` (77 contrôles sur deux Home Assistant réels reliés par Remote Home Assistant), `tests/card_test.py` (32 contrôles dans Chromium : tablette, téléphone, administrateur).
