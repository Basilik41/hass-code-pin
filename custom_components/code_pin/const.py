"""Constantes du module « Codes d'accès »."""
from __future__ import annotations

DOMAIN = "code_pin"
VERSION = "1.1.0"

CARD_URL = f"/{DOMAIN}/code-pin-card.js"
STORE_CODES = f"{DOMAIN}.codes"
STORE_JOURNAL = f"{DOMAIN}.journal"
STORE_VERSION = 1

# options
CONF_LABEL = "label"                    # étiquette des entités protégées
CONF_MODE = "mode"                      # observation | blocage
CONF_TABLET = "motif_tablette"          # texte contenu dans le nom des comptes des tablettes murales
CONF_EXEMPT = "comptes_exemptes"        # comptes jamais contrôlés (ex. intégration vocale)
CONF_LINK_USERS = "comptes_liaison"     # comptes autorisés à synchroniser / commander depuis l'autre HA
DEFAULT_LINK_NAME = "liaison"           # par défaut : comptes dont le nom contient « liaison »
CONF_NOTIFY = "notifier"                # action de notification des administrateurs
CONF_ALIASES = "alias_personnes"        # « basilik=richard » : même personne sur l'autre Home Assistant
CONF_PEER_URL = "pair_url"              # autre Home Assistant (sinon : liaisons Remote Home Assistant)
CONF_PEER_TOKEN = "pair_jeton"
CONF_ALARM_CODES = "codes_alarme"       # « alarm_control_panel.x=1234 » : code passé à la centrale

MODE_OBSERVATION = "observation"
MODE_BLOCAGE = "blocage"

DEFAULT_LABEL = "protege_par_code"
DEFAULT_LABEL_NAME = "Protégé par code"
DEFAULT_TABLET = "TabDomo"
DEFAULT_NOTIFY = "script.notifier"

PIN_MIN, PIN_MAX = 4, 8
PBKDF2_ITERATIONS = 120_000

# blocage après erreurs
MAX_FAILURES = 5                 # erreurs de suite pour une personne → blocage
LOCK_BASE = 300                  # 5 min, doublé à chaque nouveau blocage
LOCK_MAX = 3600                  # 1 h au plus
DEVICE_MAX_FAILURES = 10         # erreurs d'un même compte (toutes personnes) …
DEVICE_WINDOW = 900              # … en 15 min → compte bloqué
DEVICE_LOCK = 900

JOURNAL_MAX = 3000
JOURNAL_DAYS = 365
GATE_TTL = 600                   # durée de reconnaissance des actions faites par le module
SYNC_INTERVAL = 300
REVERT_DELAY = 2.0               # annulation d'une commande non autorisée (mode blocage)

EVENT_ACTION = f"{DOMAIN}_action"

# codes visiteur : fournis par le module « Visiteurs » (hass.data[VISITOR_PROVIDER].codes_actifs())
VISITOR_PROVIDER = "code_pin_visiteurs"
VISITOR_KEY = "visiteur"
API_SYNC = "/api/code_pin/sync"
API_EXEC = "/api/code_pin/executer"
