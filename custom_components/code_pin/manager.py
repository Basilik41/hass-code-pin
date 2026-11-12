"""Codes d'accès : vérification, exécution des actions protégées, contrôle des commandes directes, synchronisation."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.auth.models import User
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DOMAIN, ATTR_SERVICE, ATTR_SERVICE_DATA, EVENT_CALL_SERVICE
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.instance_id import async_get as async_get_instance_id
from homeassistant.helpers.storage import Store
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids

from .const import (
    API_EXEC,
    API_SYNC,
    CONF_ALARM_CODES,
    CONF_ALIASES,
    CONF_EXEMPT,
    CONF_LABEL,
    CONF_MODE,
    CONF_NOTIFY,
    CONF_PEER_TOKEN,
    CONF_PEER_URL,
    CONF_TABLET,
    DEFAULT_LABEL,
    DEFAULT_LABEL_NAME,
    DEFAULT_NOTIFY,
    DEFAULT_TABLET,
    DEVICE_LOCK,
    DEVICE_MAX_FAILURES,
    DEVICE_WINDOW,
    EVENT_ACTION,
    GATE_TTL,
    JOURNAL_DAYS,
    JOURNAL_MAX,
    MAX_FAILURES,
    MODE_BLOCAGE,
    MODE_OBSERVATION,
    REVERT_DELAY,
    STORE_CODES,
    STORE_JOURNAL,
    STORE_VERSION,
    SYNC_INTERVAL,
)
from .pin import (
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
    verb,
)

_LOGGER = logging.getLogger(__name__)
REMOTE = "remote_homeassistant"


class CodeError(HomeAssistantError):
    """Refus expliqué à l'utilisateur (code faux, bloqué…)."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


@dataclass
class Who:
    """Qui agit : la personne (si connue) et d'où."""
    kind: str                 # personne | tablette | aucun | pair
    key: str | None           # clé partagée de la personne
    name: str                 # nom affiché de la personne (ou du compte)
    device: str | None        # « Salon » pour une tablette
    user_id: str | None
    site: str | None = None   # nom de l'autre Home Assistant si l'action vient de lui

    def label(self) -> str:
        parts = [p for p in (self.device and f"tablette {self.device}", self.site) if p]
        return f"{self.name} ({', '.join(parts)})" if parts else self.name


@dataclass
class Peer:
    name: str
    url: str
    token: str
    verify_ssl: bool = True


class CodePin:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        o = {**entry.data, **entry.options}
        self.label_id: str = o.get(CONF_LABEL) or DEFAULT_LABEL
        self.mode: str = o.get(CONF_MODE) or MODE_OBSERVATION
        self.tablet_motif: str = o.get(CONF_TABLET) or DEFAULT_TABLET
        self.exempt: set[str] = set(o.get(CONF_EXEMPT) or [])
        self.notify: str = o.get(CONF_NOTIFY, DEFAULT_NOTIFY) or ""
        self.aliases = {slug(k.removeprefix("person.")): slug(v) for k, v in parse_pairs(o.get(CONF_ALIASES)).items()}
        self.alarm_codes = parse_pairs(o.get(CONF_ALARM_CODES))
        self.peer_url: str = (o.get(CONF_PEER_URL) or "").rstrip("/")
        self.peer_token: str = o.get(CONF_PEER_TOKEN) or ""
        self._codes_store: Store = Store(hass, STORE_VERSION, STORE_CODES, private=True, atomic_writes=True)
        self._journal_store: Store = Store(hass, STORE_VERSION, STORE_JOURNAL, private=True, atomic_writes=True)
        self.codes: dict[str, dict] = {}
        self.peer_users: set[str] = set()
        self.journal: list[dict] = []
        self.instance_id = ""
        self._fail: dict[str, tuple[int, int]] = {}          # personne → (erreurs de suite, niveau)
        self._dev_fail: dict[str, deque[float]] = {}         # compte → heures des erreurs
        self._dev_lock: dict[str, float] = {}
        self._gate: dict[str, float] = {}                    # contextes des actions du module → heure
        self._protected: set[str] = set()
        self._mirrors: dict[str, tuple[str, str]] = {}       # entité miroir → (entrée Remote HA, entité distante)
        self._throttle: dict[tuple[str, str], float] = {}
        self._verify_lock = asyncio.Lock()
        self._unsubs: list = []
        self.sync_status: dict[str, dict] = {}
        self._sync_lock = asyncio.Lock()
        self._sync_again = False

    # ───────── démarrage ─────────
    async def async_start(self) -> None:
        data = await self._codes_store.async_load() or {}
        self.codes = {k: v for k, v in (data.get("personnes") or {}).items() if isinstance(v, dict)}
        self.peer_users = set(data.get("comptes_pairs") or [])
        self.journal = list((await self._journal_store.async_load() or {}).get("entrees") or [])
        self.instance_id = await async_get_instance_id(self.hass)
        self._ensure_label()
        self._refresh_protected()
        self._unsubs += [
            self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._on_call),
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry),
            self.hass.bus.async_listen(lr.EVENT_LABEL_REGISTRY_UPDATED, self._on_registry),
            async_track_time_interval(self.hass, self._periodic, timedelta(seconds=SYNC_INTERVAL)),
        ]
        self.entry.async_create_background_task(self.hass, self._sync_soon(10), f"{self.entry.domain} sync")

    async def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        await self._save_codes()
        await self._journal_store.async_save({"entrees": self.journal})

    def _ensure_label(self) -> None:
        reg = lr.async_get(self.hass)
        if reg.async_get_label(self.label_id) is None and self.label_id == DEFAULT_LABEL:
            lab = reg.async_get_label_by_name(DEFAULT_LABEL_NAME) or reg.async_create(
                DEFAULT_LABEL_NAME, icon="mdi:shield-key", color="red",
                description="Actions réservées : code personnel demandé (module Codes d'accès)")
            self.label_id = lab.label_id

    @callback
    def _on_registry(self, _event: Event) -> None:
        self._refresh_protected()

    @callback
    def _refresh_protected(self) -> None:
        reg = er.async_get(self.hass)
        entries = er.async_entries_for_label(reg, self.label_id)
        self._protected = {e.entity_id for e in entries if not e.disabled_by}
        mirrors: dict[str, tuple[str, str]] = {}
        # Remote Home Assistant : unique_id = « <16 premiers caractères de l'unique_id de la liaison>_<entité locale> »
        remotes = {str(ce.unique_id or "")[:16]: ce for ce in self.hass.config_entries.async_entries(REMOTE)
                   if ce.unique_id}
        for e in entries:
            if e.platform != REMOTE:
                continue
            ce = remotes.get(str(e.unique_id).split("_", 1)[0])
            if ce is None:
                continue
            prefix = str(ce.options.get("entity_prefix") or "").lower()
            domain, obj = e.entity_id.split(".", 1)
            mirrors[e.entity_id] = (ce.entry_id, f"{domain}.{obj.replace(prefix, '', 1) if prefix else obj}")
        self._mirrors = mirrors

    def is_protected(self, entity_id: str) -> bool:
        return entity_id in self._protected

    def is_mirror(self, entity_id: str) -> bool:
        return entity_id in self._mirrors

    # ───────── personnes ─────────
    def persons(self) -> list[dict[str, Any]]:
        out = []
        for st in self.hass.states.async_all("person"):
            obj = st.entity_id.split(".", 1)[1]
            key = self.aliases.get(obj, slug(obj))
            out.append({"key": key, "entity_id": st.entity_id, "nom": st.name,
                        "photo": st.attributes.get("entity_picture"), "user_id": st.attributes.get("user_id")})
        return sorted(out, key=lambda p: p["nom"].lower())

    def person(self, key: str) -> dict[str, Any] | None:
        return next((p for p in self.persons() if p["key"] == key), None)

    def identify(self, user: User | None) -> Who:
        if user is None:
            return Who("aucun", None, "Système", None, None)
        if is_tablet(user.name, self.tablet_motif):
            return Who("tablette", None, user.name or "Tablette", tablet_place(user.name or "", self.tablet_motif),
                       user.id)
        p = next((p for p in self.persons() if p["user_id"] == user.id), None)
        if p is None:
            return Who("aucun", None, user.name or "Compte inconnu", None, user.id)
        return Who("personne", p["key"], p["nom"], None, user.id)

    async def _user_name(self, user_id: str | None) -> str:
        if not user_id:
            return "Système"
        p = next((p for p in self.persons() if p["user_id"] == user_id), None)
        if p:
            return p["nom"]
        u = await self.hass.auth.async_get_user(user_id)
        return (u.name if u else None) or "Compte inconnu"

    def locked_for(self, key: str) -> float:
        return max(0.0, float((self.codes.get(key) or {}).get("verrou") or 0) - time.time())

    # ───────── vérification d'un code ─────────
    async def verify(self, user: User | None, who: Who, key: str | None, pin: str) -> Who:
        """Vérifie le code de la personne qui agit. Sur une tablette, la personne est choisie (key)."""
        now = time.time()
        dev = user.id if user else "?"
        if self._dev_lock.get(dev, 0) > now:
            raise CodeError("appareil_bloque", "Trop d'erreurs sur cet appareil : réessayer plus tard.",
                            secondes=int(self._dev_lock[dev] - now))
        if who.kind == "tablette":
            p = self.person(key or "")
            if p is None:
                raise CodeError("personne", "Choisir une personne.")
            who = Who("tablette", p["key"], p["nom"], who.device, who.user_id)
        elif who.kind != "personne":
            raise CodeError("compte", "Ce compte n'est relié à aucune personne : action impossible ici.")
        key = who.key or ""
        async with self._verify_lock:
            left = self.locked_for(key)
            if left > 0:
                raise CodeError("bloque", f"Code de {who.name} bloqué après trop d'erreurs.", secondes=int(left))
            rec = self.codes.get(key)
            ok = await self.hass.async_add_executor_job(check_pin, pin, rec)
            if not has_code(rec):
                raise CodeError("pas_de_code", f"{who.name} n'a pas encore de code : le définir depuis son "
                                               "propre téléphone (« Mon code »).")
            if ok:
                self._fail.pop(key, None)
                self._dev_fail.pop(dev, None)
                return who
            n, level = self._fail.get(key, (0, 0))
            n, level, until = after_failure(n, level, now)
            self._fail[key] = (n, level)
            q = self._dev_fail.setdefault(dev, deque())
            q.append(now)
            while q and q[0] < now - DEVICE_WINDOW:
                q.popleft()
            dev_locked = len(q) >= DEVICE_MAX_FAILURES
            if dev_locked:
                self._dev_lock[dev] = now + DEVICE_LOCK
                q.clear()
            if until:
                rec = self.codes.setdefault(key, {})
                rec["verrou"], rec["vmaj"] = until, now
                await self._save_codes()
                self._sync_later()
        detail = f"code faux pour {who.name}" + (f" ({who.label()})" if who.device else "")
        self._log(None, "vérification du code", who, "refuse", detail)
        if until:
            mins = round((until - now) / 60)
            self._log(None, "vérification du code", who, "bloque", f"bloqué {mins} min")
            await self._alert(f"🔒 Code de {who.label()} bloqué {mins} min après {MAX_FAILURES} erreurs.")
            raise CodeError("bloque", f"Code bloqué {mins} min après trop d'erreurs.", secondes=int(until - now))
        if dev_locked:
            await self._alert(f"🔒 Appareil « {await self._user_name(user.id if user else None)} » bloqué "
                              f"{DEVICE_LOCK // 60} min : trop de codes faux.")
            raise CodeError("appareil_bloque", "Trop d'erreurs sur cet appareil : réessayer plus tard.",
                            secondes=DEVICE_LOCK)
        raise CodeError("code_faux", "Code incorrect.", essais=MAX_FAILURES - n)

    # ───────── actions ─────────
    def describe(self, entity_id: str) -> dict[str, Any]:
        st = self.hass.states.get(entity_id)
        domain = entity_id.split(".", 1)[0]
        feats = st.attributes.get("supported_features") if st else None
        acts = entity_actions(domain, st.state if st else None, feats if isinstance(feats, int) else None)
        return {
            "protege": self.is_protected(entity_id), "distant": self.is_mirror(entity_id),
            "actions": [{"cle": k, "libelle": a.label,
                         "code": action_needs_code(domain, k, st.state if st else None)} for k, a in acts.items()],
        }

    async def execute(self, user: User | None, entity_id: str, action: str, key: str | None,
                      pin: str | None) -> dict[str, Any]:
        if not self.is_protected(entity_id):
            raise CodeError("non_protege", "Cette entité n'est pas protégée par code.")
        st = self.hass.states.get(entity_id)
        domain = entity_id.split(".", 1)[0]
        feats = st.attributes.get("supported_features") if st else None
        acts = entity_actions(domain, st.state if st else None, feats if isinstance(feats, int) else None)
        if action not in acts:
            raise CodeError("action", "Action impossible pour cet appareil.")
        who = self.identify(user)
        if action_needs_code(domain, action, st.state if st else None):
            who = await self.verify(user, who, key, pin or "")
        elif who.kind == "aucun" and user is not None:
            raise CodeError("compte", "Ce compte n'est relié à aucune personne : action impossible ici.")
        elif who.kind == "tablette":
            who = Who("tablette", None, "Quelqu'un", who.device, who.user_id)  # action sans code : personne non choisie
        name = (st.name if st else None) or entity_id
        if self.is_mirror(entity_id):
            try:
                res = await self._forward(entity_id, action, who)
            except CodeError as err:
                self._log(entity_id, acts[action].label, who, "erreur", err.message, name=name)
                raise
            self._log(entity_id, acts[action].label, who, "ok", f"via {res.get('site') or 'l’autre Home Assistant'}",
                      name=name)
            return {"ok": True, "qui": who.label()}
        await self._run(entity_id, acts[action].service, who)
        self._log(entity_id, acts[action].label, who, "ok", name=name)
        return {"ok": True, "qui": who.label()}

    async def _run(self, entity_id: str, service: str, who: Who, extra: dict | None = None) -> None:
        domain = entity_id.split(".", 1)[0]
        data: dict[str, Any] = {"entity_id": entity_id, **(extra or {})}
        if domain == "alarm_control_panel" and entity_id in self.alarm_codes and "code" not in data:
            data["code"] = self.alarm_codes[entity_id]
        ctx = Context(user_id=who.user_id)
        self._gate_add(ctx.id)
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=ctx)
        except HomeAssistantError as err:
            self._log(entity_id, verb(service), who, "erreur", str(err))
            raise CodeError("erreur", f"L'appareil a refusé : {err}") from err

    def _gate_add(self, ctx_id: str) -> None:
        now = time.monotonic()
        self._gate[ctx_id] = now
        if len(self._gate) > 500:
            self._gate = {k: v for k, v in self._gate.items() if now - v < GATE_TTL}

    def _from_gate(self, ctx: Context) -> bool:
        now = time.monotonic()
        for cid in (ctx.id, ctx.parent_id):
            if cid and cid in self._gate and now - self._gate[cid] < GATE_TTL:
                return True
        return False

    # ───────── contrôle des commandes directes ─────────
    @callback
    def _on_call(self, event: Event) -> None:
        """Appelé avant l'exécution de chaque action : repère celles qui touchent une entité protégée sans code."""
        ctx = event.context
        if not self._protected or ctx.user_id is None or ctx.user_id in self.exempt or self._from_gate(ctx):
            return
        data = event.data.get(ATTR_SERVICE_DATA) or {}
        if not any(k in data for k in ("entity_id", "device_id", "area_id", "floor_id", "label_id")):
            return
        try:
            sel = async_extract_referenced_entity_ids(self.hass, TargetSelection(data))
        except Exception:  # noqa: BLE001
            return
        hits = (sel.referenced | sel.indirectly_referenced) & self._protected
        sdomain, service = event.data.get(ATTR_DOMAIN, ""), event.data.get(ATTR_SERVICE, "")
        for eid in hits:
            if sdomain not in ("homeassistant", eid.split(".", 1)[0]):
                continue  # ex. homeassistant.turn_off par étiquette : HA rappelle chaque domaine avec la même cible
            st = self.hass.states.get(eid)
            prev = st.state if st else None
            if call_is_sensitive(eid.split(".", 1)[0], sdomain, service, prev):
                self.entry.async_create_task(
                    self.hass, self._violation(eid, prev, ctx.user_id, f"{sdomain}.{service}"), eager_start=False)

    async def _violation(self, entity_id: str, prev: str | None, user_id: str, service: str) -> None:
        st = self.hass.states.get(entity_id)
        name = (st.name if st else None) or entity_id
        via_peer = user_id in self.peer_users  # compte du jeton de liaison : commande venue peut-être de l'autre HA
        uname = await self._user_name(user_id)
        who = Who("compte", None, f"{uname} (ou via l'autre Home Assistant)" if via_peer else uname, None, user_id)
        mirror = self.is_mirror(entity_id)
        blocking = self.mode == MODE_BLOCAGE and not mirror  # un miroir : l'autre Home Assistant annule chez lui
        undo = revert_call(entity_id.split(".", 1)[0], prev, self.alarm_codes.get(entity_id)) if blocking else None
        if blocking and undo:
            async_call_later(self.hass, REVERT_DELAY, self._make_revert(entity_id, undo, who))
        result = "annule" if undo else ("non_annulable" if blocking else "observe")
        self._log(entity_id, verb(service), who, "commande_directe", {
            "annule": "annulée", "non_annulable": "impossible à annuler", "observe": "mode observation : laissée"}[result],
            name=name)
        k = (user_id, entity_id)
        if time.monotonic() - self._throttle.get(k, -1e9) < 60:
            return
        self._throttle[k] = time.monotonic()
        tail = {"annule": "Commande annulée.", "non_annulable": "Annulation impossible pour cet appareil.",
                "observe": "Mode observation : rien n'a été annulé."}[result]
        await self._alert(f"⚠️ {name} : {verb(service)} par {who.name} sans passer par le code. {tail}")

    def _make_revert(self, entity_id: str, undo: tuple[str, dict], who: Who):
        @callback
        def _do(_now: Any) -> None:
            self.entry.async_create_task(self.hass, self._revert(entity_id, undo, who))
        return _do

    async def _revert(self, entity_id: str, undo: tuple[str, dict], who: Who) -> None:
        try:
            await self._run(entity_id, undo[0], Who("aucun", None, "Codes d'accès", None, None), undo[1])
        except CodeError as err:
            _LOGGER.warning("Annulation impossible sur %s : %s", entity_id, err.message)
            await self._alert(f"❗ Annulation impossible sur {entity_id} : {err.message}")

    # ───────── gestion des codes ─────────
    async def set_code(self, user: User, new: str, old: str | None = None, target: str | None = None,
                       admin_pin: str | None = None) -> None:
        who = self.identify(user)
        if target and target != who.key:
            if not user.is_admin:
                raise CodeError("droits", "Réservé aux administrateurs.")
            await self._check_admin(user, who, admin_pin)
            p = self.person(target)
            if p is None:
                raise CodeError("personne", "Personne inconnue.")
            key, label = target, p["nom"]
        else:
            if who.kind != "personne":
                raise CodeError("compte", "Définir son code depuis son propre compte (pas depuis une tablette).")
            key, label = who.key or "", who.name
            if has_code(self.codes.get(key)):
                await self.verify(user, who, None, old or "")
        problem = pin_problem(new)
        if problem:
            raise CodeError(problem, {"chiffres": "Le code ne doit contenir que des chiffres.",
                                      "longueur": "Le code doit avoir de 4 à 8 chiffres.",
                                      "trop_simple": "Code trop facile à deviner (suite, chiffres répétés…)."}[problem])
        rec = await self.hass.async_add_executor_job(hash_pin, new)
        now = time.time()
        self.codes[key] = {**rec, "maj": now, "par": who.name, "nom": label, "verrou": 0, "vmaj": now}
        self._fail.pop(key, None)
        await self._save_codes()
        self._log(None, "code", who, "ok", f"code de {label} " + ("défini par un administrateur" if key != who.key
                                                                      else "défini"))
        self._sync_later()

    async def reset_code(self, user: User, target: str, admin_pin: str | None) -> None:
        who = self.identify(user)
        if not user.is_admin:
            raise CodeError("droits", "Réservé aux administrateurs.")
        await self._check_admin(user, who, admin_pin)
        p = self.person(target)
        label = p["nom"] if p else target
        now = time.time()
        self.codes[target] = {"efface": True, "maj": now, "par": who.name, "nom": label, "verrou": 0, "vmaj": now}
        self._fail.pop(target, None)
        await self._save_codes()
        self._log(None, "code", who, "ok", f"code de {label} effacé")
        self._sync_later()

    async def unlock(self, user: User, target: str, admin_pin: str | None) -> None:
        who = self.identify(user)
        if not user.is_admin:
            raise CodeError("droits", "Réservé aux administrateurs.")
        await self._check_admin(user, who, admin_pin)
        rec = self.codes.setdefault(target, {})
        rec["verrou"], rec["vmaj"] = 0, time.time()
        self._fail.pop(target, None)
        self._dev_lock.clear()
        await self._save_codes()
        self._log(None, "code", who, "ok", f"code de {(self.person(target) or {}).get('nom', target)} débloqué")
        self._sync_later()

    async def _check_admin(self, user: User, who: Who, admin_pin: str | None) -> None:
        if who.kind != "personne":
            raise CodeError("compte", "Gestion des codes depuis son propre compte administrateur.")
        if not has_code(self.codes.get(who.key or "")):
            raise CodeError("admin_sans_code", "Définir d'abord votre propre code.")
        await self.verify(user, who, None, admin_pin or "")

    async def _save_codes(self) -> None:
        await self._codes_store.async_save({"personnes": self.codes, "comptes_pairs": sorted(self.peer_users)})

    # ───────── journal ─────────
    def _log(self, entity_id: str | None, action: str, who: Who, result: str, detail: str = "",
             name: str | None = None) -> None:
        now = time.time()
        e = {"t": round(now, 1), "entite": entity_id, "nom_entite": name, "action": action,
             "personne": who.name, "appareil": who.device, "site": who.site, "resultat": result, "detail": detail}
        self.journal.append(e)
        cutoff = now - JOURNAL_DAYS * 86400
        if len(self.journal) > JOURNAL_MAX or (self.journal and self.journal[0]["t"] < cutoff):
            self.journal = [x for x in self.journal if x["t"] >= cutoff][-JOURNAL_MAX:]
        self._journal_store.async_delay_save(lambda: {"entrees": self.journal}, 10)
        if entity_id:
            self.hass.bus.async_fire(EVENT_ACTION, {"entity_id": entity_id, "action": action, "qui": who.label(),
                                                    "resultat": result, "detail": detail})

    # ───────── alertes ─────────
    async def _alert(self, message: str) -> None:
        svc = self.notify.strip()
        try:
            if svc.count(".") == 1:
                dom, name = svc.split(".")
                if self.hass.services.has_service(dom, name):
                    data = ({"titre": "Codes d'accès", "message": message, "niveau": "admin"} if dom == "script"
                            else {"title": "Codes d'accès", "message": message})
                    await self.hass.services.async_call(dom, name, data, blocking=False)
                    return
            await self.hass.services.async_call("persistent_notification", "create",
                                                {"title": "Codes d'accès", "message": message}, blocking=False)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Alerte non envoyée : %s", message)

    # ───────── autre Home Assistant ─────────
    def peers(self) -> list[Peer]:
        if self.peer_url and self.peer_token:
            return [Peer("Autre Home Assistant", self.peer_url, self.peer_token)]
        out = []
        for ce in self.hass.config_entries.async_entries(REMOTE):
            d = ce.data
            if ce.disabled_by or not d.get("host") or not d.get("access_token"):
                continue
            scheme = "https" if d.get("secure") else "http"
            out.append(Peer(ce.title or d["host"], f"{scheme}://{d['host']}:{d.get('port', 8123)}",
                            d["access_token"], bool(d.get("verify_ssl", False))))
        return out

    def _peer_for_entry(self, entry_id: str) -> Peer | None:
        ce = self.hass.config_entries.async_get_entry(entry_id)
        if ce is None:
            return None
        if self.peer_url and self.peer_token:
            return Peer(ce.title or "Autre Home Assistant", self.peer_url, self.peer_token)
        d = ce.data
        if not d.get("host") or not d.get("access_token"):
            return None
        scheme = "https" if d.get("secure") else "http"
        return Peer(ce.title or d["host"], f"{scheme}://{d['host']}:{d.get('port', 8123)}", d["access_token"],
                    bool(d.get("verify_ssl", False)))

    async def _post(self, peer: Peer, path: str, payload: dict) -> dict:
        session = async_get_clientsession(self.hass, verify_ssl=peer.verify_ssl)
        try:
            async with session.post(f"{peer.url}{path}", json=payload,
                                    headers={"Authorization": f"Bearer {peer.token}"},
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 404:
                    raise CodeError("pair_absent", "Module absent sur l'autre Home Assistant.")
                if r.status in (401, 403):
                    raise CodeError("pair_droits", "Le jeton de liaison n'est pas celui d'un administrateur.")
                body = await r.json(content_type=None)
                if r.status >= 400:
                    raise CodeError((body or {}).get("code", "pair_erreur"),
                                    (body or {}).get("message", f"Erreur {r.status} de l'autre Home Assistant."))
                return body or {}
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise CodeError("pair_injoignable", f"Autre Home Assistant injoignable ({type(err).__name__}).") from err

    async def _forward(self, entity_id: str, action: str, who: Who) -> dict:
        entry_id, remote = self._mirrors[entity_id]
        peer = self._peer_for_entry(entry_id)
        if peer is None:
            raise CodeError("pair_absent", "Liaison avec l'autre Home Assistant introuvable.")
        return await self._post(peer, API_EXEC, {
            "entity_id": remote, "action": action, "personne": who.key, "nom": who.name, "appareil": who.device,
            "site": self.hass.config.location_name})

    async def exec_from_peer(self, user: User, data: dict) -> dict:
        """Action demandée par l'autre Home Assistant, code déjà vérifié là-bas (jeton administrateur)."""
        self._learn_peer(user)
        eid, action = data["entity_id"], data["action"]
        if not self.is_protected(eid):
            raise CodeError("non_protege", f"{eid} n'est pas protégée ici.")
        st = self.hass.states.get(eid)
        domain = eid.split(".", 1)[0]
        feats = st.attributes.get("supported_features") if st else None
        acts = entity_actions(domain, st.state if st else None, feats if isinstance(feats, int) else None)
        if action not in acts:
            raise CodeError("action", "Action impossible pour cet appareil.")
        p = self.person(str(data.get("personne") or ""))
        who = Who("pair", p["key"] if p else None, str(data.get("nom") or (p or {}).get("nom") or "Inconnu"),
                  data.get("appareil"), user.id, str(data.get("site") or "autre Home Assistant"))
        name = (st.name if st else None) or eid
        await self._run(eid, acts[action].service, who)
        self._log(eid, acts[action].label, who, "ok", name=name)
        return {"ok": True, "site": self.hass.config.location_name}

    def _learn_peer(self, user: User) -> None:
        if user.id not in self.peer_users:
            self.peer_users.add(user.id)
            self.hass.async_create_task(self._save_codes())

    async def sync_from_peer(self, user: User, theirs: dict) -> dict:
        self._learn_peer(user)
        self.codes, changed = merge_records(self.codes, theirs)
        if changed:
            await self._save_codes()
        return {"personnes": self.codes, "instance": self.instance_id, "site": self.hass.config.location_name}

    async def sync_all(self) -> None:
        if self._sync_lock.locked():
            self._sync_again = True
            return
        async with self._sync_lock:
            while True:
                self._sync_again = False
                for peer in self.peers():
                    try:
                        body = await self._post(peer, API_SYNC, {"personnes": self.codes,
                                                                 "instance": self.instance_id})
                        if body.get("instance") == self.instance_id:
                            raise CodeError("pair_soi", "La liaison pointe vers ce même Home Assistant.")
                        self.codes, changed = merge_records(self.codes, body.get("personnes") or {})
                        if changed:
                            await self._save_codes()
                        self.sync_status[peer.name] = {"ok": True, "t": time.time(), "site": body.get("site")}
                    except CodeError as err:
                        self.sync_status[peer.name] = {"ok": False, "t": time.time(), "erreur": err.message}
                if not self._sync_again:
                    break

    def _sync_later(self) -> None:
        self.entry.async_create_background_task(self.hass, self._sync_soon(0), "code_pin sync")

    async def _sync_soon(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        await self.sync_all()

    async def _periodic(self, _now: Any) -> None:
        now = time.monotonic()
        self._gate = {k: v for k, v in self._gate.items() if now - v < GATE_TTL}
        self._throttle = {k: v for k, v in self._throttle.items() if now - v < 600}
        self._dev_lock = {k: v for k, v in self._dev_lock.items() if v > time.time()}
        await self.sync_all()

