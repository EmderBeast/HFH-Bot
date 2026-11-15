"""Brett beim Exercising lesen: welche Felder liegen vor der Figur, und welcher Weg bringt am meisten?

Das Brett ist ein Raster aus Rauten. Vom Feld der Figur aus liegen die Nachbarfelder bei ±(160, 80) und ±(160, -80)
(Referenz 1600 x 900, gemessen am Video vom 12.11.2026). Jedes Feld hat ein Symbol; der Bot erkennt es an Vorlagen
in bilder/felder/. Höher oder tiefer liegende Wegstücke passen nicht ins Raster; dort hört das Vorausschauen auf.

Was die Felder bringen (vom Nutzer, 15.11.2026): Diamanten geben nur Story-Fragen (Filmklappe) und Geschenke, das
Ziel einmal 20. Das Fragezeichen löscht eine zufällige Karte, der Pfeil gibt einen Action Point. Die Bananenschale kostet einen Action Point. Die Hand mit Schweiß (Stamina Drain: 3 Runden nur
1 Schritt pro Zug) ist vor dem Ziel schlecht (man kommt vielleicht nicht mehr hin), danach egal oder sogar gut,
wenn gute Felder folgen.
"""
from __future__ import annotations

import glob
import os

import cv2
import numpy as np

REF_W, REF_H = 1600, 900
U = np.array([160.0, 80.0])          # nach rechts unten
V = np.array([160.0, -80.0])         # nach rechts oben
DIRS = [U, -U, V, -V]
HERE = os.path.dirname(os.path.abspath(__file__))

# Wert eines Felds in „Diamanten“. Ein Action Point ist etwa einen Wurf wert, also etwa 4.
VALUE = {
    'filmklappe': 6.0,      # Story-Frage: 5 oder 10 Diamanten oder eine Karte
    'geschenk': 6.0,        # Geschenk: Diamanten
    'frage': -2.0,          # nimmt eine zufällige Karte aus dem Inventar und löscht sie (Nutzer, 15.11.2026)
    'shop': 0.0,
    'pfeil': 4.0,           # +1 Action Point (Nutzer, 15.11.2026)
    'hantel': -6.0,         # jeder Wurf kostet 2 statt 1 Action Point (Nutzer, 15.11.2026)
    'wirbel': -3.0,         # die Figur läuft in zufällige Richtungen, man kann nicht mehr wählen (Nutzer, 15.11.2026)
    'leer': 0.0,
    'start': 0.0,
    'ziel': 20.0,           # FINISH: einmal 20 Diamanten (danach 0)
    'unbekannt': -3.0,      # Symbol, das der Bot nicht kennt (z. B. das Hütchen: 4 oder 8 Action Points weg)
    'banane': -4.0,         # kostet einen Action Point
    'schweiss': -6.0,       # Stamina Drain vor dem Ziel; nach dem Ziel 0
}
UNKNOWN = 1.5               # Feld nicht zu sehen (Rand, andere Höhe): ein Durchschnittsfeld
GOOD = 5.0                  # ab hier ist ein Feld „gut“
UI_BOXES = [(0, 0, 330, 120), (50, 60, 240, 240), (630, 15, 970, 95), (1170, 25, 1600, 150), (0, 690, 1000, 900),
            (1150, 560, 1600, 900)]   # Titel, Action Points, Status, Karten, Würfel: dort nichts lesen

_TPL = None


def templates():
    global _TPL
    if _TPL is None:
        _TPL = []
        for p in sorted(glob.glob(os.path.join(HERE, 'bilder', 'felder', '*.png'))):
            im = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
            if im is not None:
                _TPL.append((os.path.basename(p).rsplit('_', 1)[0], im.astype(np.float32)))
    return _TPL


def ref(img):
    if img.shape[1] == REF_W and img.shape[0] == REF_H:
        return img
    return cv2.resize(img, (REF_W, REF_H), interpolation=cv2.INTER_AREA)


class Board:
    def __init__(self, img, spot, goal_visible=True):
        self.im = ref(img)
        self.hsv = cv2.cvtColor(self.im, cv2.COLOR_BGR2HSV)
        self.gray = cv2.cvtColor(self.im, cv2.COLOR_BGR2GRAY).astype(np.float32)
        # gelbe Pfeile und Figur stören das Symbol: diese Bildpunkte durch das Grau des Felds ersetzen
        h = self.hsv
        yellow = (h[..., 0] >= 15) & (h[..., 0] <= 35) & (h[..., 1] >= 120) & (h[..., 2] >= 170)
        self.gray[yellow] = 215
        self.yellow = yellow
        self.c = np.array([float(spot[0]), float(spot[1]) + 15.0])
        self.cache = {}
        self.goal_visible = goal_visible      # Zielfahne auf dem Brett zu sehen (sonst ist Blau nur Wasser)

    def visible(self, p):
        x, y = p
        if not (90 <= x <= REF_W - 90 and 60 <= y <= REF_H - 60):
            return False
        return not any(x0 <= x <= x1 and y0 <= y <= y1 for (x0, y0, x1, y1) in UI_BOXES)

    def cell(self, p):
        """Art des Felds bei p: Name, 'unsichtbar' oder None (kein Weg: Gras, Wasser, Sand ...)."""
        key = (int(round(p[0])), int(round(p[1])))
        if key in self.cache:
            return self.cache[key]
        out = None
        if not self.visible(p):
            out = 'unsichtbar'
        else:
            x, y = key
            hs = self.hsv[y - 40:y + 40, x - 70:x + 70]
            ok = ~self.yellow[y - 40:y + 40, x - 70:x + 70]
            if ok.sum() < 800:
                self.cache[key] = 'unsichtbar'
                return 'unsichtbar'
            s, v = np.median(hs[..., 1][ok]), np.median(hs[..., 2][ok])
            if s < 40 and 160 < v < 245:                 # graue Wegplatte
                crop = self.gray[y - 45:y + 45, x - 75:x + 75]
                best, kind = 0.0, 'leer'
                for name, t in templates():
                    if name == 'ziel' and not self.goal_visible:
                        continue
                    r = cv2.matchTemplate(crop, t[6:-6, 10:-10], cv2.TM_CCOEFF_NORMED).max()
                    if r > best:
                        best, kind = float(r), name
                blue = ((hs[..., 0] >= 100) & (hs[..., 0] <= 118) & (hs[..., 1] >= 90) & (hs[..., 2] < 215)).mean()
                if blue > 0.10 and self.goal_visible:
                    out = 'ziel'                 # die karierte Zielfahne steht auf oder über diesem Feld
                elif best >= 0.5:
                    out = kind
                else:
                    # ein Symbol ist da (dunkle Linien), passt aber zu keiner Vorlage: Vorsicht
                    out = 'unbekannt' if (crop < 150).mean() > 0.08 else 'leer'
        self.cache[key] = out
        return out

    def paths(self, start, d, depth):
        """Alle Wege ab dem Feld der Figur in Richtung d, je höchstens depth Felder: Listen von Feldarten."""
        out = []

        def walk(p, came, seq):
            if len(seq) >= depth:
                out.append(seq)
                return
            nxt = []
            for dd in DIRS:
                if came is not None and np.allclose(dd, -came):
                    continue
                q = p + dd
                k = self.cell(q)
                if k is not None:
                    nxt.append((q, dd, k))
            if not nxt:
                out.append(seq)
                return
            for q, dd, k in nxt:
                if k == 'unsichtbar':
                    out.append(seq + ['unsichtbar'] * (depth - len(seq)))
                else:
                    walk(q, dd, seq + [k])

        k0 = self.cell(start + d)
        if k0 is None or k0 == 'unsichtbar':     # Weg da (Pfeil), aber nicht lesbar (andere Höhe, verdeckt)
            return [['unsichtbar'] * depth]
        walk(start + d, d, [k0])
        return out


def value(kind, after_goal):
    if kind == 'unsichtbar':
        return UNKNOWN
    if kind in ('schweiss', 'ziel') and after_goal:
        return 0.0
    return VALUE.get(kind, 0.0)


def path_score(seq, steps, after_goal):
    """Erwarteter Wert: Landung nach 1..max(steps) Schritten (gleich wahrscheinlich), dazu ein kleiner Teil der Felder
    dahinter (für die nächsten Züge)."""
    if not seq:
        return -99.0
    land = [value(seq[s - 1], after_goal) if s - 1 < len(seq) else -2.0 for s in steps]
    later = [value(k, after_goal) for k in seq[max(steps):max(steps) + 4]]
    return float(np.mean(land)) + (0.3 * float(np.mean(later)) if later else 0.0)


def dir_of(spot, arrow):
    a = np.array(arrow, float) - np.array([spot[0], spot[1] + 15.0])
    return max(DIRS, key=lambda d: float(np.dot(a, d) / (np.linalg.norm(a) * np.linalg.norm(d) + 1e-9)))


def rate_arrows(img, spot, arrows, steps=(1, 2, 3), after_goal=False, goal_angle=None, goal_weight=0.0, goal_visible=False):
    """Bewertet jeden Pfeil. Gibt [(Wert, Pfeil, bester Weg)] absteigend sortiert zurück."""
    b = Board(img, spot, goal_visible)
    depth = max(steps) + 4
    out = []
    seen = []
    for a in arrows:
        d = dir_of(spot, a)
        if any(np.allclose(d, e) for e in seen):     # derselbe Weg zweimal (Pfeil pulsiert)
            continue
        seen.append(d)
        ps = b.paths(b.c, d, depth) or [[]]
        best = max(ps, key=lambda s: path_score(s, steps, after_goal))
        sc = path_score(best, steps, after_goal)
        if goal_angle is not None and goal_weight:
            ang = np.arctan2(d[1], d[0])
            sc += goal_weight * float(np.cos(ang - goal_angle))
        out.append((sc, a, best))
    out.sort(key=lambda x: -x[0])
    return out


def forward_tiles(img, spot, depth=7):
    """Für die Karten: die Felder in allen Richtungen ab der Figur (bester Weg je Richtung)."""
    b = Board(img, spot, False)
    res = []
    for d in DIRS:
        ps = b.paths(b.c, d, depth)
        res.extend(ps)
    return res


def read_dial(reader, img):
    """Die möglichen Würfe auf der Scheibe unten rechts (z. B. 1 2 3, mit Sports Drink 2 4 6)."""
    try:
        boxes = reader.read(img, (1310, 560, 1580, 690))
    except Exception:
        return (1, 2, 3)
    vals = sorted({int(b.text) for b in boxes if b.text.strip().isdigit() and 1 <= int(b.text) <= 16})
    return tuple(vals) if len(vals) >= 2 else (1, 2, 3)


def card_plan(img, spot, after_goal):
    """Was die Felder voraus für die Karten bedeuten. Gibt (doppelt, schlecht_voraus, Text) zurück:
    doppelt = Sports Drink lohnt (gutes Feld erst 4 bis 6 Felder weit, keins in 1 bis 3);
    schlecht_voraus = in 1 bis 3 Feldern liegt nichts Gutes, aber mindestens zwei schlechte Felder
    (dann Energy Candy: 8 Schritte darüber hinweg, ohne Action Points)."""
    near = far = -9.0
    bad = 0
    for seq in forward_tiles(img, spot, 7):
        nb = 0
        for i, k in enumerate(seq):
            v = value(k, after_goal) if k != 'unsichtbar' else -9.0
            if i < 3:
                near = max(near, v)
                nb += 1 if (k != 'unsichtbar' and v < 0) else 0
            elif i < 6:
                far = max(far, v)
        bad = max(bad, nb)
    return far >= GOOD and near < GOOD, near <= 0 and bad >= 2, f'nah {near:g}, weit {far:g}, schlecht {bad}'
