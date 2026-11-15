"""Die festen Strecken der vier Exercising-Kurse (aus den Spieldaten) und wo die Figur gerade steht.

treasuregird.json: jedes Wegfeld einer Karte als rankNumber = Zeile * 100 + Spalte, dazu das Startfeld (birthPoint)
und ob dort ein Ereignis liegen kann (appearTreasure). Nachbarn sind Zeile ± 1 und Spalte ± 1.
Auf dem Bildschirm (Referenz 1600 x 900) ist Zeile + 1 = (160, 80) und Spalte + 1 = (160, -80) (Spielcode: Euler
60/-180/-225; geprüft am Video vom 12.11.2026: das erste Bild ortet genau auf das Startfeld 1216 von Marathon).

Welche Symbole auf den Feldern liegen, verteilt das Spiel bei jedem Lauf neu. Der Bot merkt sich, was er sieht.
"""
from __future__ import annotations

import json
import os
from collections import deque

import numpy as np

import exboard as XB

HERE = os.path.dirname(os.path.abspath(__file__))
MAP_IDS = {1: 1001, 2: 1002, 3: 1003, 4: 1004}       # Sprint Training, Jogging, Distance Run, Marathon


def _load(name):
    with open(os.path.join(HERE, 'exercising_daten', name), encoding='utf-8') as f:
        return json.load(f)


class Course:
    def __init__(self, map_id):
        self.id = map_id
        grid = [r for r in _load('treasuregird.json') if r['mapsNumber'] == map_id]
        self.cells = {r.get('rankNumber', 0) for r in grid}
        self.events = {r.get('rankNumber', 0) for r in grid if r.get('appearTreasure')}
        st = [r.get('rankNumber', 0) for r in grid if r.get('birthPoint')]
        self.start = st[0] if st else None
        self.nb = {c: [n for n in (c - 100, c + 100, c - 1, c + 1) if n in self.cells and abs(n % 100 - c % 100) <= 1]
                   for c in self.cells}

    def dist_from(self, a):
        d = {a: 0}
        q = deque([a])
        while q:
            c = q.popleft()
            for n in self.nb[c]:
                if n not in d:
                    d[n] = d[c] + 1
                    q.append(n)
        return d

    def score_at(self, obs, origin):
        s = 0.0
        for i, j, road in obs:
            c = origin + i * 100 + j
            inmap = c in self.cells and 0 <= (origin % 100) + j < 100
            s += (1.0 if inmap else -1.5) if road else (-1.0 if inmap else 0.5)
        return s

    def locate(self, obs, near=None, reach=12):
        """Bestes Feld für die Figur. near: letzte bekannte Position (dann nur Felder in Reichweite). Gibt
        (Feld, Punkte, Abstand zum Zweitbesten) zurück."""
        if near is not None and near in self.cells:
            d = self.dist_from(near)
            cand = [c for c, k in d.items() if k <= reach]
        else:
            cand = list(self.cells)
        res = sorted(((self.score_at(obs, c), c) for c in cand), reverse=True)
        if not res:
            return None, 0.0, 0.0
        second = res[1][0] if len(res) > 1 else -99.0
        return res[0][1], res[0][0], res[0][0] - second


def observe(img, spot, goal_visible=False):
    """Was der Bot um die Figur herum sieht: [(Zeile, Spalte relativ, Weg ja/nein, Art)] und das Board."""
    b = XB.Board(img, spot, goal_visible)
    out = []
    for i in range(-5, 6):
        for j in range(-5, 6):
            if i == 0 and j == 0:
                out.append((0, 0, True, None))      # die Figur steht auf einem Weg
                continue
            p = b.c + i * XB.U + j * XB.V
            if not b.visible(p):
                continue
            k = b.cell(p)
            if k == 'unsichtbar':
                continue
            out.append((i, j, k is not None, k))
    return out, b


def guess_course(obs):
    """Welcher Kurs passt am besten (für einen Lauf, der schon läuft)?"""
    best = None
    for mid in MAP_IDS.values():
        c = Course(mid)
        pos, sc, gap = c.locate([(i, j, r) for i, j, r, _ in obs])
        if best is None or sc > best[1]:
            best = (mid, sc, pos)
    return best


class Tracker:
    """Merkt sich Position, Laufrichtung und die gesehenen Symbole eines Laufs."""

    def __init__(self, course: Course):
        self.course = course
        self.pos = None
        self.prev = None            # Feld davor (nicht zurück laufen)
        self.kinds = {}             # Feld -> Art des Symbols (die am häufigsten gesehene)
        self.counts = {}            # Feld -> {Art: wie oft gesehen}
        self.goal = None            # Zielfeld, sobald gesehen
        self.goal_sure = False      # über den Fuß der Fahnenstange bestimmt

    def update(self, obs, log=None):
        o3 = [(i, j, r) for i, j, r, _ in obs]
        pos, sc, gap = self.course.locate(o3, near=self.pos)
        if pos is None or sc < 6 or gap < (3 if self.pos is None else 1.5):
            # unsicher: lieber ohne Karte weiterspielen als falsch planen
            if log:
                log(f'    (Karte: Position unsicher, Punkte {sc:.1f}, Abstand {gap:.1f})')
            return False
        if self.pos is not None and pos != self.pos:
            # Feld davor: Nachbar der neuen Position auf dem kürzesten Weg von der alten
            d = self.course.dist_from(self.pos)
            nbs = [n for n in self.course.nb[pos] if n in d]
            if nbs:
                self.prev = min(nbs, key=lambda n: d[n])
        self.pos = pos
        for i, j, road, k in obs:
            c = pos + i * 100 + j
            if road and k and k not in ('unsichtbar',) and c in self.course.cells and (i, j) != (0, 0):
                cnt = self.counts.setdefault(c, {})
                cnt[k] = cnt.get(k, 0) + 1
                self.kinds[c] = max(cnt, key=cnt.get)
        zs = [(cnt.get('ziel', 0), c) for c, cnt in self.counts.items() if cnt.get('ziel')]
        if zs and not self.goal_sure:
            self.goal = max(zs)[1]
        return True

    def kind(self, c):
        k = self.kinds.get(c)
        if k is None:
            return 'unsichtbar' if c in self.course.events else 'leer'
        return k

    def walks(self, first, came, depth):
        """Alle Wege ab dem Nachbarfeld first (ohne Umkehren), je depth Felder: Listen von Feldern."""
        out = []

        def go(c, back, seq):
            if len(seq) >= depth:
                out.append(seq)
                return
            nxt = [n for n in self.course.nb[c] if n != back]
            if not nxt:
                out.append(seq)
                return
            for n in nxt:
                go(n, c, seq + [n])
        go(first, came, [first])
        return out

    def branch_value(self, first, steps, after_goal, goal_weight=0.0):
        """Wert eines Wegs ab dem Nachbarfeld first: Landung nach 1..max(steps) Schritten, an weiteren Abzweigungen
        der bessere Weg; dazu die Felder dahinter und, solange das Ziel noch fehlt, wie nah es rückt."""
        depth = max(steps) + 4
        best = None
        dgoal = self.course.dist_from(self.goal) if (self.goal and goal_weight) else None
        for seq in self.walks(first, self.pos, depth):
            kinds = [self.kind(c) for c in seq]
            # das Ziel zählt schon beim Darüberlaufen (Spieldaten)
            v = XB.path_score(kinds, steps, after_goal)
            if dgoal is not None:
                here = dgoal.get(self.pos, 99)
                there = min(dgoal.get(c, 99) for c in seq[:max(steps)])
                v += goal_weight * (here - there) / max(1, max(steps))
                if self.goal in seq[:max(steps)]:
                    v += goal_weight
            if best is None or v > best[0]:
                best = (v, seq, kinds)
        return best

    def neighbor_for(self, d):
        """Nachbarfeld in Bildschirmrichtung d (eine von XB.DIRS)."""
        if self.pos is None:
            return None
        if np.allclose(d, XB.U):
            n = self.pos + 100
        elif np.allclose(d, -XB.U):
            n = self.pos - 100
        elif np.allclose(d, XB.V):
            n = self.pos + 1
        else:
            n = self.pos - 1
        return n if n in self.course.cells else None

    def forward(self):
        """Nachbarfelder, in die die Figur laufen kann (nicht zurück)."""
        if self.pos is None:
            return []
        return [n for n in self.course.nb[self.pos] if n != self.prev]

    def landing_options(self, steps=(1, 2, 3), after_goal=False):
        """Für den Steps Setter: je Schrittzahl der beste erreichbare Wert (an Abzweigungen wählt der Bot)."""
        res = {}
        for f in self.forward():
            for seq in self.walks(f, self.pos, max(steps)):
                for s in steps:
                    if s - 1 < len(seq):
                        k = self.kind(seq[s - 1])
                        # das Ziel zählt schon beim Darüberlaufen, dafür braucht es keine genaue Schrittzahl
                        v = 0.0 if k == 'ziel' else XB.value(k, after_goal)
                        res[s] = max(res.get(s, -99.0), v)
        return res

    def set_goal_from_flag(self, base, spot):
        """Fuß der Fahnenstange (Bildpunkt) → Zielfeld."""
        if self.pos is None:
            return
        c = np.array([spot[0], spot[1] + 15.0])
        m = np.array([XB.U, XB.V]).T                 # Bild = c + i*U + j*V
        i, j = np.linalg.solve(m, np.array(base, float) - c)
        cell = self.pos + int(round(i)) * 100 + int(round(j))
        if cell in self.course.cells and abs(i - round(i)) < 0.35 and abs(j - round(j)) < 0.35:
            self.goal = cell
            self.goal_sure = True
            self.kinds[cell] = 'ziel'
