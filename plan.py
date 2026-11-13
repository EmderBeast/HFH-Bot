"""Aufstellungen: welche Teile welcher Spieler tragen soll.

Liest zwei Formate:
1. Den Team-Export des CC-Simulators ("Speichern" -> "Kopieren"): {"name", "team": {"slots", "libero", "bench"}, "cc"}.
   Genutzt werden die Spieler, deren Potentials im Simulator als Kreis mit echten Teilen vorliegen (z. B. nach
   "Anziehen" im Potential-Rechner). Spieler mit einer Vorlage (Potential-Guide, ohne einzelne Teile) werden übersprungen.
2. Das Bot-Format (z. B. aus einem Export-Knopf im Potential-Rechner):
   {"potbot": 1, "name": "...", "players": [{"id": 5701, "pieces": [{"slot": "I", "set": "...", "lv": 12,
     "main": "Power", "rolls": {"Strength": 3, ...}, "from": 5702}, ...]}]}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from texts import T
from vision import SLOTS, Piece, main_stat_of, set_from_text, sub_base

HERE = os.path.dirname(os.path.abspath(__file__))
# gamedata.json: from the CC-Simulator's data (tools/potbot/mkdata.js), with names, skills, memories, bonds and cheers
with open(os.path.join(HERE, 'gamedata.json'), encoding='utf-8') as f:
    GAME = json.load(f)
NAMES = {int(k): v['name'] for k, v in GAME['chars'].items()}

SUB_KEYS = {'QuickFlat', 'QuickPct', 'PowerFlat', 'PowerPct', 'SetFlat', 'SetPct', 'ServeFlat', 'ServePct',
            'ReceiveFlat', 'ReceivePct', 'BlockFlat', 'BlockPct', 'SaveFlat', 'SavePct',
            'Awareness', 'Strength', 'Reflex', 'Spirit', 'AtkTech', 'DefTech'}
MAIN_KEYS = {'Power', 'Quick', 'Set', 'Serve', 'Receive', 'Block', 'Save', 'Awareness', 'Strength', 'Reflex',
             'Spirit', 'AtkTech', 'DefTech'}


class PlanError(ValueError):
    pass


@dataclass
class PlayerPlan:
    id: int
    name: str
    targets: dict = field(default_factory=dict)   # Platz -> Piece
    source: dict = field(default_factory=dict)    # Platz -> Spieler-ID, der das Teil laut Simulator gerade trägt
    role: str = ''
    memory: tuple | None = None                   # (Memory-ID, Stufe 1-5), die der Spieler tragen soll


@dataclass
class Loadout:
    name: str
    players: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (Name, Grund)
    warnings: list = field(default_factory=list)

    def summary(self) -> str:
        n = sum(len(p.targets) for p in self.players)
        m = sum(1 for p in self.players if p.memory)
        return T('{name}: {players} Spieler, {pieces} Teile, {mems} Memories', name=self.name, players=len(self.players),
                 pieces=n, mems=m)


def _rolls(raw: dict) -> dict:
    out = {}
    for k, v in (raw or {}).items():
        if k not in SUB_KEYS:
            raise PlanError(T('Unbekannter Nebenwert {key}', key=repr(k)))
        v = float(v)
        n = round(v)
        if abs(v - n) > 0.01:
            # ältere Speicherstände: Summe statt Würfe
            n = round(v / sub_base(k))
        if n > 0:
            out[k] = int(n)
    return out


def _piece(slot: str, raw: dict) -> Piece:
    st = set_from_text(str(raw.get('set', ''))) or raw.get('set')
    lv = int(round(float(raw.get('lv', 12))))
    main = main_stat_of(slot, st, raw.get('main'))
    if main not in MAIN_KEYS:
        raise PlanError(T('Unbekannter Hauptwert {main} auf Platz {slot}', main=repr(main), slot=slot))
    return Piece(slot=slot, set=st, lv=lv, main=main, rolls=_rolls(raw.get('rolls') if 'rolls' in raw else raw.get('subs')))


def _name(pid: int, given: str | None = None) -> str:
    return given or NAMES.get(pid) or f'ID {pid}'


def from_team_export(obj: dict, name: str | None = None) -> Loadout:
    team = obj.get('team') or {}
    lo = Loadout(name or obj.get('name') or 'Team')
    members = []
    for k in sorted((team.get('slots') or {}).keys(), key=lambda x: int(x) if str(x).isdigit() else 99):
        members.append((f'Pos {k}', team['slots'][k]))
    if team.get('libero'):
        members.append(('Libero', team['libero']))
    for i, b in enumerate(team.get('bench') or []):
        if b:
            members.append((f'Bank {i + 1}', b))
    seen = set()
    for role, m in members:
        if not m or not m.get('id'):
            continue
        pid = int(m['id'])
        if pid in seen:
            continue
        seen.add(pid)
        pp = PlayerPlan(pid, _name(pid), role=role)
        mem = m.get('memory') or {}
        if mem.get('id'):
            pp.memory = (int(mem['id']), max(1, min(5, int(mem.get('lv') or mem.get('tier') or 1))))
        pot = m.get('pot') or {}
        slots = pot.get('slots')
        why = None
        if not isinstance(slots, list) or not any(isinstance(x, dict) and x.get('set') for x in slots):
            if pot:
                why = T('keine einzelnen Teile im Simulator (Vorlage). Erst im Potential-Rechner „Anziehen“.')
        elif pot.get('src') == 'list':
            why = T('im Simulator gilt die Liste, nicht der Kreis. Erst im Potential-Rechner „Anziehen“.')
        elif not pot.get('own') and not any(isinstance(x, dict) and x.get('uid') for x in slots):
            why = T('der Kreis ist eine Vorlage, keine echten Teile. Erst im Potential-Rechner „Anziehen“.')
        else:
            # Hat der Kreis Teile aus dem Inventar (mit uid), dann sind Teile ohne uid daneben keine echten Teile, sondern
            # Reste einer Vorlage: Der Bot suchte bei Iwaizumi drei Assist-Receive-Teile mit denselben Nebenwerten, die es
            # im Spiel nicht gibt (Lauf „Test2“ vom 11.11.2026). Solche Plätze bleiben im Spiel, wie sie sind (1.15).
            mixed = any(isinstance(x, dict) and x.get('uid') for x in slots)
            left = []
            for i, raw in enumerate(slots[:6]):
                if raw and raw.get('set'):
                    if mixed and not raw.get('uid'):
                        left.append(SLOTS[i])
                        continue
                    pp.targets[SLOTS[i]] = _piece(SLOTS[i], raw)
            if left:
                lo.warnings.append(T('{name}: Platz {slots} im Simulator ist kein Teil aus deinem Inventar (Vorlage); der Bot lässt ihn im Spiel, wie er ist.',
                                     name=_name(pid), slots=', '.join(left)))
        if why:
            lo.skipped.append((_name(pid), T('Potentials: {why}', why=why)))
        if pp.targets or pp.memory:
            lo.players.append(pp)
    return lo


def from_bot_format(obj: dict, name: str | None = None) -> Loadout:
    lo = Loadout(name or obj.get('name') or T('Aufstellung'))
    seen = set()
    for p in obj.get('players') or []:
        pid = int(p['id'])
        if pid in seen:
            raise PlanError(T('{name} steht doppelt in der Aufstellung', name=_name(pid)))
        seen.add(pid)
        pp = PlayerPlan(pid, _name(pid, p.get('name')), role=p.get('role', ''))
        for raw in p.get('pieces') or []:
            slot = str(raw.get('slot', '')).upper()
            if slot not in SLOTS:
                raise PlanError(T('{name}: unbekannter Platz {slot}', name=pp.name, slot=repr(slot)))
            pp.targets[slot] = _piece(slot, raw)
            if raw.get('from'):
                pp.source[slot] = int(raw['from'])
        if p.get('memory'):
            pp.memory = (int(p['memory']['id']), int(p['memory'].get('tier') or 1))
        if pp.targets or pp.memory:
            lo.players.append(pp)
    return lo


def parse(text: str, name: str | None = None) -> Loadout:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise PlanError(T('Kein gültiger Text aus dem Simulator ({msg}).', msg=e.msg))
    if isinstance(obj, dict) and obj.get('potbot'):
        lo = from_bot_format(obj, name)
    elif isinstance(obj, dict) and isinstance(obj.get('team'), dict):
        lo = from_team_export(obj, name)
    else:
        raise PlanError(T('Unbekanntes Format. Erwartet: Team-Export des Simulators (Speichern -> Kopieren).'))
    if not lo.players:
        why = '; '.join(f'{n}: {r}' for n, r in lo.skipped) or T('kein Spieler mit Potentials')
        raise PlanError(T('Die Aufstellung enthält keine Teile und keine Memories ({why}).', why=why))
    check_conflicts(lo)
    return lo


def parse_many(texts: list, name: str | None = None) -> Loadout:
    """Mehrere Texte aus dem Simulator (z. B. drei Teams) zu einer Aufstellung zusammenlegen.
    Steht ein Spieler in mehreren Teams, gelten die Teile aus dem ersten."""
    objs = []
    for text in texts:
        try:
            objs.append(json.loads(text))
        except json.JSONDecodeError as e:
            raise PlanError(T('Kein gültiger Text aus dem Simulator ({msg}).', msg=e.msg))
    if len(objs) == 1:
        return parse(texts[0], name)
    lo = Loadout(name or T('Aufstellung'))
    seen = {}
    for i, obj in enumerate(objs, 1):
        if isinstance(obj, dict) and obj.get('potbot'):
            part = from_bot_format(obj)
        elif isinstance(obj, dict) and isinstance(obj.get('team'), dict):
            part = from_team_export(obj)
        else:
            raise PlanError(T('Text {i}: unbekanntes Format.', i=i))
        for p in part.players:
            if p.id in seen:
                lo.warnings.append(T('{name} steht in Text {a} und {b}; es gelten die Teile aus Text {a}.', name=p.name, a=seen[p.id], b=i))
                continue
            seen[p.id] = i
            lo.players.append(p)
        lo.skipped += part.skipped
    if not lo.players:
        raise PlanError(T('Die Aufstellung enthält keine Teile.'))
    extra = lo.warnings
    check_conflicts(lo)
    lo.warnings = extra + lo.warnings
    return lo


def check_conflicts(lo: Loadout):
    """Zwei Spieler mit demselben Teil gehen nur, wenn es das Teil zweimal gibt; das prüft erst das Spiel.
    Hier nur eine Warnung im Ergebnis, kein Abbruch."""
    seen = {}
    lo.warnings = []
    for p in lo.players:
        for s, pc in p.targets.items():
            key = (s, pc.set, pc.lv, pc.main, tuple(sorted(pc.rolls.items())))
            if key in seen:
                lo.warnings.append(T('{a} und {b} brauchen auf Platz {slot} ein gleiches Teil ({set}). '
                                     'Das klappt nur, wenn du es zweimal hast.', a=p.name, b=seen[key], slot=s, set=pc.set))
            else:
                seen[key] = p.name


def to_bot_format(lo: Loadout) -> dict:
    return {'potbot': 1, 'name': lo.name, 'players': [
        dict({'id': p.id, 'name': p.name, 'role': p.role}, **({'memory': {'id': p.memory[0], 'tier': p.memory[1]}} if p.memory else {}),
             pieces=[
            dict({'slot': s, 'set': t.set, 'lv': t.lv, 'main': t.main, 'rolls': dict(t.rolls)},
                 **({'from': p.source[s]} if s in p.source else {}))
            for s, t in sorted(p.targets.items(), key=lambda x: SLOTS.index(x[0]))])
        for p in lo.players]}


def save(lo: Loadout, folder: str, name: str) -> str:
    """Speichert die Aufstellung im Bot-Format unter aufstellungen/<name>.json."""
    os.makedirs(folder, exist_ok=True)
    safe = ''.join(c for c in name if c.isalnum() or c in ' -_').strip() or 'Aufstellung'
    path = os.path.join(folder, safe + '.json')
    lo.name = name
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(to_bot_format(lo), f, ensure_ascii=False, indent=1)
    return path
