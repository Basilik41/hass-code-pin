"""Tests des fonctions pures (python -m pytest tests/test_pin.py)."""
import sys
import types
from pathlib import Path

_pkg = types.ModuleType("code_pin")  # paquet sans son __init__ (pas de Home Assistant ici)
_pkg.__path__ = [str(Path(__file__).parents[1] / "custom_components" / "code_pin")]
sys.modules["code_pin"] = _pkg

from code_pin.pin import (
    action_needs_code,
    after_failure,
    call_is_sensitive,
    check_pin,
    entity_actions,
    has_code,
    hash_pin,
    is_tablet,
    merge_records,
    parse_pairs,
    pin_problem,
    revert_call,
    slug,
    tablet_place,
)


def test_pin_problem():
    assert pin_problem("4827") is None
    assert pin_problem("90517263") is None
    for bad, why in (("123", "longueur"), ("123456789", "longueur"), ("12a4", "chiffres"), ("", "chiffres"),
                     ("0000", "trop_simple"), ("1234", "trop_simple"), ("2345", "trop_simple"),
                     ("9876", "trop_simple"), ("7890", "trop_simple"), ("123456", "trop_simple"),
                     ("１２３４", "chiffres"), (None, "chiffres")):
        assert pin_problem(bad) == why, bad


def test_hash_and_check():
    rec = hash_pin("4827")
    assert check_pin("4827", rec)
    assert not check_pin("4828", rec)
    assert not check_pin("", rec)
    assert not check_pin("4827", None)
    assert not check_pin("4827", {**rec, "efface": True})
    assert not check_pin("4827", {"h": "zz", "s": "yy"})
    assert not check_pin("x" * 1000, rec)
    assert has_code(rec) and not has_code({"efface": True}) and not has_code(None)
    assert hash_pin("4827")["s"] != rec["s"]  # sel aléatoire


def test_after_failure():
    n, lvl, until = 0, 0, None
    for _ in range(4):
        n, lvl, until = after_failure(n, lvl, 1000)
        assert until is None
    n, lvl, until = after_failure(n, lvl, 1000)
    assert (n, lvl, until) == (0, 1, 1300)
    for _ in range(5):
        n, lvl, until = after_failure(n, lvl, 2000)
    assert until == 2600
    lvl = 10
    n, lvl, until = after_failure(4, lvl, 0)
    assert until == 3600  # plafond 1 h


def test_merge():
    a = {"julie": {**hash_pin("4827"), "maj": 10, "verrou": 0, "vmaj": 10}}
    b = {"julie": {**hash_pin("5555"), "maj": 20, "verrou": 0, "vmaj": 5},
         "val": {**hash_pin("3141"), "maj": 3}}
    m, ch = merge_records(a, b)
    assert ch and m["julie"]["h"] == b["julie"]["h"] and "val" in m
    m2, ch2 = merge_records(m, a)
    assert not ch2 and m2["julie"]["h"] == b["julie"]["h"]  # plus ancien : ignoré
    # blocage : le plus récent gagne, déblocage compris
    m3, _ = merge_records(m2, {"julie": {"verrou": 999, "vmaj": 50, "maj": 0}})
    assert m3["julie"]["verrou"] == 999 and m3["julie"]["h"] == b["julie"]["h"]
    m4, _ = merge_records(m3, {"julie": {"verrou": 0, "vmaj": 60, "maj": 0}})
    assert m4["julie"]["verrou"] == 0
    # effacement propagé
    m5, _ = merge_records(m4, {"julie": {"efface": True, "maj": 99}})
    assert m5["julie"]["efface"] and "h" not in m5["julie"]
    # clés invalides ignorées
    m6, ch6 = merge_records({}, {"../x": {"maj": 1}, "Julie": {"maj": 1}, "ok": "pas un dict"})
    assert m6 == {} and not ch6
    # égalité : même résultat des deux côtés
    x = {"k": {"h": "aa", "maj": 5}}
    y = {"k": {"h": "bb", "maj": 5}}
    assert merge_records(x, y)[0]["k"]["h"] == merge_records(y, x)[0]["k"]["h"] == "bb"


def test_misc():
    assert slug("Élodie Dupont") == "elodie_dupont"
    assert parse_pairs("basilik=richard\n person.x = y ,bad, =z") == {"basilik": "richard", "person.x": "y"}
    assert is_tablet("TabDomo Salon", "TabDomo") and is_tablet("tabdomo-cuisine", "TabDomo")
    assert not is_tablet("Julie", "TabDomo") and not is_tablet(None, "TabDomo")
    assert tablet_place("TabDomo Salon", "TabDomo") == "Salon"
    assert tablet_place("Cuisine - tabdomo", "TabDomo") == "Cuisine"
    assert tablet_place("TabDomo", "TabDomo") == "TabDomo"


def test_actions():
    assert set(entity_actions("cover", "closed", 15)) == {"ouvrir", "fermer", "stop"}
    assert set(entity_actions("cover", "closed", 3)) == {"ouvrir", "fermer"}
    assert "ouvrir" not in entity_actions("lock", "locked", 0)
    assert set(entity_actions("alarm_control_panel", "disarmed", 3)) == {"desarmer", "armer_absent", "armer_present"}
    assert entity_actions("sensor", "1", None) == {}
    assert action_needs_code("cover", "ouvrir", "closed") and not action_needs_code("cover", "fermer", "open")
    assert not action_needs_code("cover", "stop", "opening")  # arrêter une porte : jamais de code
    assert not action_needs_code("alarm_control_panel", "armer_absent", "disarmed")
    assert action_needs_code("alarm_control_panel", "armer_present", "armed_away")
    assert action_needs_code("switch", "activer", "off") and action_needs_code("switch", "couper", "on")
    assert action_needs_code("lock", "inconnue", "locked")


def test_sensitive():
    assert call_is_sensitive("cover", "cover", "open_cover", "closed")
    assert not call_is_sensitive("cover", "cover", "close_cover", "open")
    assert not call_is_sensitive("cover", "cover", "stop_cover", "opening")
    assert call_is_sensitive("cover", "cover", "toggle", "closed")
    assert not call_is_sensitive("cover", "cover", "toggle", "open")
    assert call_is_sensitive("cover", "cover", "set_cover_position", "closed")
    assert call_is_sensitive("cover", "homeassistant", "turn_on", "closed")
    assert not call_is_sensitive("cover", "homeassistant", "turn_off", "open")
    assert call_is_sensitive("cover", "homeassistant", "toggle", "closed")
    assert not call_is_sensitive("lock", "homeassistant", "turn_off", "locked")
    assert not call_is_sensitive("alarm_control_panel", "homeassistant", "turn_off", "armed_away")
    assert call_is_sensitive("lock", "lock", "unlock", "locked") and not call_is_sensitive("lock", "lock", "lock", "x")
    assert call_is_sensitive("alarm_control_panel", "alarm_control_panel", "alarm_disarm", "armed_away")
    assert not call_is_sensitive("alarm_control_panel", "alarm_control_panel", "alarm_arm_away", "disarmed")
    assert call_is_sensitive("alarm_control_panel", "alarm_control_panel", "alarm_arm_home", "armed_away")
    assert call_is_sensitive("switch", "switch", "turn_off", "on") and call_is_sensitive("switch", "switch", "turn_on", "off")
    assert call_is_sensitive("input_boolean", "homeassistant", "toggle", "on")
    assert not call_is_sensitive("switch", "homeassistant", "update_entity", "on")
    assert call_is_sensitive("button", "button", "press", "unknown")


def test_revert():
    assert revert_call("cover", "closed", None) == ("close_cover", {})
    assert revert_call("cover", "open", None) is None
    assert revert_call("lock", "locked", None) == ("lock", {})
    assert revert_call("alarm_control_panel", "armed_away", "1234") == ("alarm_arm_away", {"code": "1234"})
    assert revert_call("alarm_control_panel", "armed_night", None) == ("alarm_arm_night", {})
    assert revert_call("alarm_control_panel", "disarmed", None) is None
    assert revert_call("switch", "on", None) == ("turn_on", {})
    assert revert_call("input_boolean", "off", None) == ("turn_off", {})
    assert revert_call("switch", "unavailable", None) is None
    assert revert_call("button", "unknown", None) is None
