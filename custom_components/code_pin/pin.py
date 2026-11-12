"""Fonctions pures (sans Home Assistant) : codes, blocages, actions sensibles, fusion entre instances."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from .const import LOCK_BASE, LOCK_MAX, MAX_FAILURES, PBKDF2_ITERATIONS, PIN_MAX, PIN_MIN

# ───────── codes ─────────
_COMMON = {
    "1234", "4321", "1212", "2580", "0852", "1004", "2000", "6969", "1122", "1313", "2468", "1357",
    "12345", "54321", "123456", "654321", "121212", "112233", "123123", "1234567", "12345678", "87654321",
}


def pin_problem(pin: str) -> str | None:
    """Raison pour laquelle un nouveau code est refusé, ou None. Les codes trop faciles sont refusés."""
    if not isinstance(pin, str) or not pin.isascii() or not pin.isdigit():
        return "chiffres"
    if not PIN_MIN <= len(pin) <= PIN_MAX:
        return "longueur"
    if len(set(pin)) == 1 or pin in _COMMON:
        return "trop_simple"
    steps = {(int(b) - int(a)) % 10 for a, b in pairwise(pin)}
    if steps in ({1}, {9}):  # 2345, 9876, 7890…
        return "trop_simple"
    return None


def hash_pin(pin: str, salt: bytes | None = None, iterations: int = PBKDF2_ITERATIONS) -> dict[str, Any]:
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return {"h": h.hex(), "s": salt.hex(), "i": iterations}


def check_pin(pin: str, rec: dict[str, Any] | None) -> bool:
    """Toujours le même temps de calcul, qu'un code existe ou non (pas d'indice sur l'existence d'un code)."""
    if not isinstance(pin, str) or not pin.isdigit() or len(pin) > 32:
        pin = "x"
    if not rec or rec.get("efface") or not rec.get("h"):
        hashlib.pbkdf2_hmac("sha256", pin.encode(), b"\0" * 16, PBKDF2_ITERATIONS)
        return False
    try:
        salt = bytes.fromhex(rec["s"])
        want = bytes.fromhex(rec["h"])
        iterations = int(rec.get("i") or PBKDF2_ITERATIONS)
    except (KeyError, TypeError, ValueError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
    return hmac.compare_digest(got, want)


def has_code(rec: dict[str, Any] | None) -> bool:
    return bool(rec and rec.get("h") and not rec.get("efface"))


# ───────── blocages ─────────
def after_failure(failures: int, level: int, now: float) -> tuple[int, int, float | None]:
    """Erreur de code : (erreurs de suite, niveau de blocage, bloqué jusqu'à). 5 min, 10, 20, 40, puis 1 h."""
    failures += 1
    if failures < MAX_FAILURES:
        return failures, level, None
    level += 1
    return 0, level, now + min(LOCK_BASE * 2 ** (level - 1), LOCK_MAX)


# ───────── fusion entre les deux Home Assistant ─────────
_CODE_FIELDS = ("h", "s", "i", "maj", "par", "efface", "nom")
_LOCK_FIELDS = ("verrou", "vmaj")


def merge_records(mine: dict[str, dict], theirs: dict[str, dict]) -> tuple[dict[str, dict], bool]:
    """Le code le plus récent gagne (« maj ») ; le blocage le plus récent aussi (« vmaj », pour qu'un
    déblocage par un administrateur se propage). Renvoie (fusion, changé ?)."""
    out = {k: dict(v) for k, v in mine.items()}
    changed = False
    for key, t in (theirs or {}).items():
        if not isinstance(t, dict) or not _valid_key(key):
            continue
        m = out.get(key)
        if m is None:
            out[key] = {f: t[f] for f in (*_CODE_FIELDS, *_LOCK_FIELDS) if f in t}
            changed = True
            continue
        if _newer(t, m):
            for f in _CODE_FIELDS:
                if f in t:
                    m[f] = t[f]
                else:
                    m.pop(f, None)
            changed = True
        if float(t.get("vmaj") or 0) > float(m.get("vmaj") or 0):
            for f in _LOCK_FIELDS:
                m[f] = t.get(f) or 0
            changed = True
    return out, changed


def _newer(a: dict, b: dict) -> bool:
    ta, tb = float(a.get("maj") or 0), float(b.get("maj") or 0)
    if ta != tb:
        return ta > tb
    return str(a.get("h") or "") > str(b.get("h") or "")  # égalité : choix stable des deux côtés


_KEY = re.compile(r"^[a-z0-9_]{1,64}$")


def _valid_key(key: Any) -> bool:
    return isinstance(key, str) and bool(_KEY.match(key))


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:64]


def parse_pairs(text: str | None) -> dict[str, str]:
    """« a=b » une paire par ligne (ou séparées par des virgules) → {a: b}. Lignes mal formées ignorées."""
    out: dict[str, str] = {}
    for part in re.split(r"[\n,;]+", text or ""):
        if "=" in part:
            a, b = part.split("=", 1)
            a, b = a.strip(), b.strip()
            if a and b:
                out[a] = b
    return out


# ───────── tablettes ─────────
def is_tablet(user_name: str | None, motif: str) -> bool:
    return bool(user_name and motif and motif.lower() in user_name.lower())


def tablet_place(user_name: str, motif: str) -> str:
    """« TabDomo Salon » → « Salon » ; « TabDomo » seul → « TabDomo »."""
    rest = re.sub(re.escape(motif), "", user_name, flags=re.IGNORECASE).strip(" -_:·.")
    return rest or user_name.strip()


# ───────── actions ─────────
@dataclass(frozen=True)
class Action:
    service: str
    label: str
    needs_code: bool


_ONOFF = {"activer": Action("turn_on", "Activer", True), "couper": Action("turn_off", "Couper", True)}
ACTIONS: dict[str, dict[str, Action]] = {
    "cover": {"ouvrir": Action("open_cover", "Ouvrir", True), "fermer": Action("close_cover", "Fermer", False),
              "stop": Action("stop_cover", "Stop", False)},
    "lock": {"deverrouiller": Action("unlock", "Déverrouiller", True),
             "verrouiller": Action("lock", "Verrouiller", False), "ouvrir": Action("open", "Ouvrir", True)},
    "alarm_control_panel": {
        "desarmer": Action("alarm_disarm", "Désarmer", True),
        "armer_absent": Action("alarm_arm_away", "Armer (absent)", False),
        "armer_present": Action("alarm_arm_home", "Armer (présent)", False),
        "armer_nuit": Action("alarm_arm_night", "Armer (nuit)", False),
        "armer_vacances": Action("alarm_arm_vacation", "Armer (vacances)", False),
    },
    "valve": {"ouvrir": Action("open_valve", "Ouvrir", True), "fermer": Action("close_valve", "Fermer", True)},
    "switch": _ONOFF, "input_boolean": _ONOFF, "light": _ONOFF, "fan": _ONOFF, "siren": _ONOFF,
    "automation": _ONOFF,
    "button": {"appuyer": Action("press", "Appuyer", True)},
    "input_button": {"appuyer": Action("press", "Appuyer", True)},
}
_ALARM_FEATURES = {"armer_present": 1, "armer_absent": 2, "armer_nuit": 4, "armer_vacances": 32}
_ARMED = ("armed_away", "armed_home", "armed_night", "armed_vacation", "armed_custom_bypass")


def entity_actions(domain: str, state: str | None, features: int | None) -> dict[str, Action]:
    acts = dict(ACTIONS.get(domain, {}))
    if domain == "alarm_control_panel" and features is not None:
        acts = {k: a for k, a in acts.items() if k not in _ALARM_FEATURES or features & _ALARM_FEATURES[k]}
    if domain == "lock" and features is not None and not features & 1:
        acts.pop("ouvrir", None)
    if domain == "cover" and features is not None:
        for k, bit in (("ouvrir", 1), ("fermer", 2), ("stop", 8)):
            if not features & bit:
                acts.pop(k, None)
    return acts


def action_needs_code(domain: str, key: str, state: str | None) -> bool:
    act = ACTIONS.get(domain, {}).get(key)
    if act is None:
        return True
    if domain == "alarm_control_panel" and key.startswith("armer") and state in _ARMED:
        return True  # passer d'« absent » à « présent » revient à désarmer une partie
    return act.needs_code


_SAFE_SERVICES = {"update_entity", "reload", "reload_config_entry"}
_SAFE_BY_DOMAIN = {
    "cover": {"close_cover", "stop_cover", "close_cover_tilt", "stop_cover_tilt"},
    "lock": {"lock"},
    "valve": {"stop_valve"},
}


def call_is_sensitive(entity_domain: str, service_domain: str, service: str, state: str | None) -> bool:
    """Une commande sur une entité protégée doit-elle passer par le code ? (contrôle des commandes directes)"""
    if service in _SAFE_SERVICES:
        return False
    if service_domain == "homeassistant":
        if entity_domain in ("lock", "alarm_control_panel", "button", "input_button", "valve"):
            return False  # homeassistant.turn_on/off/toggle n'agit pas sur ces domaines
        if entity_domain == "cover":
            service = {"turn_on": "open_cover", "turn_off": "close_cover"}.get(service, service)
    if service in _SAFE_BY_DOMAIN.get(entity_domain, set()):
        return False
    if entity_domain == "cover" and service == "toggle":
        return state in ("closed", "closing", None)
    if entity_domain == "lock" and service == "toggle":
        return state in ("locked", "locking", None)
    if entity_domain == "alarm_control_panel" and service.startswith("alarm_arm_"):
        return state in _ARMED or state in ("triggered", "pending", "arming")
    return True


def revert_call(entity_domain: str, prev_state: str | None, alarm_code: str | None) -> tuple[str, dict] | None:
    """Commande qui remet l'entité dans l'état d'avant (mode blocage), ou None si rien n'est possible."""
    if prev_state in (None, "unknown", "unavailable"):
        return None
    if entity_domain == "cover":
        return ("close_cover", {}) if prev_state in ("closed", "closing") else None
    if entity_domain == "lock":
        return ("lock", {}) if prev_state in ("locked", "locking") else None
    if entity_domain == "valve":
        return ("close_valve", {}) if prev_state in ("closed", "closing") else None
    if entity_domain == "alarm_control_panel":
        if prev_state not in _ARMED:
            return None
        data = {"code": alarm_code} if alarm_code else {}
        return ("alarm_" + prev_state.replace("armed_", "arm_"), data)
    if entity_domain in ("switch", "input_boolean", "light", "fan", "siren", "automation"):
        return ("turn_on" if prev_state == "on" else "turn_off", {}) if prev_state in ("on", "off") else None
    return None


_VERBS = {
    "open_cover": "ouverture", "set_cover_position": "ouverture", "toggle": "basculement", "close_cover": "fermeture",
    "unlock": "déverrouillage", "open": "ouverture", "lock": "verrouillage", "alarm_disarm": "désarmement",
    "alarm_arm_home": "armement (présent)", "alarm_arm_away": "armement (absent)", "alarm_arm_night": "armement (nuit)",
    "alarm_arm_vacation": "armement (vacances)", "alarm_trigger": "déclenchement", "turn_off": "arrêt",
    "turn_on": "mise en marche", "press": "appui", "trigger": "déclenchement", "open_valve": "ouverture",
    "close_valve": "fermeture",
}


def verb(service: str) -> str:
    """« cover.open_cover » → « ouverture » (alertes et journal lisibles par toute la famille)."""
    return _VERBS.get(service.split(".")[-1], service)
