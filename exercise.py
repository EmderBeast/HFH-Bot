"""Exercising-Bot: spielt die Läufe in „Free Time → Exercising“ (Brettspiel mit Würfel, Story-Fragen, Shop und Karten).

Was der Bot tippt (und sonst nichts):
  - im Fenster „Exercising Courses“: „Continue“ oder „Enter“ beim gewählten Kurs, solange „Exercising Left“ über 0 ist
  - auf dem Brett: „GO!“ (würfeln), an Abzweigungen erst den gelben Pfeil Richtung Zielfahne; am Zielfeld den rechten
    Knopf (weiterlaufen; der linke beendet den Lauf, und die übrigen Action Points verfallen), danach weg vom Ziel;
    auf Wunsch die Karten unten links
  - bei Karten: „Use“ nur für Karten aus der Liste unten (Energy Candy, Lightweight Runners, Sports Drink, Multiplier)
  - „Story“: die Antwort mit Diamanten (sonst einer nützlichen Karte) laut Spieldaten (exercising_fragen.json); „Event“: „Complete“; „Obtained“: „Tap to close“; „FINISH“: „Claim & Exit“
  - „Shop“: immer „Don't Buy“. Der Bot kauft nie etwas, auch nicht mit Action Points.
Vor jedem Tippen liest er die Stelle und bricht ab, wenn dort etwas wie Purchase, Gems, Diamonds, Recharge steht.

Aufgebaut nach dem Video „Exercising.mkv“ vom 12.11.2026 (Marathon, 20 Runden, 13 Action Points).
"""
from __future__ import annotations

import os
import random
import re
import time

import difflib
import glob

import cv2
import numpy as np

import exboard as XB
import exmap as XM
import layout as L
from device import SafetyStop
from texts import T
from vision import REF_H, REF_W, find, norm

# ---------------------------------------------------------------- Stellen (Referenz 1600 x 900, gemessen am Video)
GO = (1462, 728)                    # „GO!“ in der Mitte der Scheibe unten rechts
CARD_SLOTS = [(245, 790), (408, 790), (570, 790), (733, 790)]   # Karten unten links (nach „Storage“)
CARD_BOX = 70                       # halbe Breite des Ausschnitts, mit dem der Bot eine Karte wiedererkennt
BOARD = (200, 130, 1300, 780)       # Bereich, in dem die gelben Pfeile an Abzweigungen stehen
STILL = (125, 120, 1200, 760)       # Bereich für „bewegt sich noch etwas?“
COURSE_X = [315, 642, 969, 1294]    # „Enter“-Knöpfe der vier Kurse
COURSE_NAMES = ['Sprint Training', 'Jogging', 'Distance Run', 'Marathon']

# Karten, die der Bot benutzt (Name im Fenster, ohne Leerzeichen, klein). Alles andere lässt er liegen.
GOOD_CARDS = {
    'energycandy': 'Energy Candy (8 Schritte ohne Action Points)',
    'lightweightrunners': 'Lightweight Runners (Züge kosten 2 Runden lang 1 AP weniger)',
    'sportsdrink': 'Sports Drink (2 Runden doppelt so viele Schritte)',
}
GOOD_PARTS = ['multipl']            # Multiplier-Karte (mehr Belohnung am Ende), Name unbekannt

# Wörter, die an einer Stelle, auf die der Bot tippt, nie stehen dürfen
BAD_PARTS = ['purchas', 'recharg', 'refill', 'topup', 'diamond', 'gem', 'recruit', 'quickunequ', 'dismant', 'sell',
             'exchang', 'store', 'pay', 'buy']
OK_PARTS = ['dontbuy', 'donotbuy']  # „Don't Buy“ ist erlaubt


def tap_text_ok(text: str, allow=()) -> bool:
    n = norm(text)
    for ok in OK_PARTS:
        n = n.replace(ok, '')
    return not any(b in n for b in BAD_PARTS if b not in allow)


# ---------------------------------------------------------------- Bild auswerten

def yellow_arrows(img: np.ndarray) -> list[tuple[int, int]]:
    """Die großen gelben Pfeile, die an einer Abzweigung um die Figur stehen. Gibt die Mitte jedes Pfeils
    (Referenz-Koordinaten). Getestet am Video: nur an Abzweigungen, nie sonst."""
    im = cv2.resize(img, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 18) & (hsv[..., 0] <= 32) & (hsv[..., 1] >= 150) & (hsv[..., 2] >= 200)).astype(np.uint8)
    x0, y0, x1, y1 = BOARD
    mask = np.zeros_like(m)
    mask[y0:y1, x0:x1] = m[y0:y1, x0:x1]
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, st, c = cv2.connectedComponentsWithStats(mask)
    blobs = [(float(c[i][0]), float(c[i][1]), int(st[i, 4])) for i in range(1, n) if st[i, 4] > 280]
    # ein Pfeil besteht aus zwei Streifen: Teile, die näher als 85 px beieinander liegen, zusammenfassen
    groups: list[list] = []
    for b in sorted(blobs, key=lambda b: -b[2]):
        for g in groups:
            if any((b[0] - o[0]) ** 2 + (b[1] - o[1]) ** 2 < 85 ** 2 for o in g):
                g.append(b)
                break
        else:
            groups.append([b])
    out = []
    for g in groups:
        w = sum(b[2] for b in g)
        if w < 500:
            continue
        out.append((int(sum(b[0] * b[2] for b in g) / w), int(sum(b[1] * b[2] for b in g) / w)))
    return out


HERE = os.path.dirname(os.path.abspath(__file__))
_TPL = {}


def _tpl(name):
    if name not in _TPL:
        _TPL[name] = cv2.imread(os.path.join(HERE, 'bilder', name))
    return _TPL[name]


def _match(im, tpl, scales):
    best = (0.0, None, 1.0)
    if tpl is None:
        return best
    for sc in scales:
        t = cv2.resize(tpl, None, fx=sc, fy=sc)
        r = cv2.matchTemplate(im, t, cv2.TM_CCOEFF_NORMED)
        _, m, _, loc = cv2.minMaxLoc(r)
        if m > best[0]:
            best = (float(m), (loc[0] + t.shape[1] // 2, loc[1] + t.shape[0] // 2), sc)
    return best


def _pointer(im, c):
    """Richtung der Spitze am Kreis mit der Zielfahne (Grad, Bildschirm: 0 = rechts, 90 = unten). Nur Bildpunkte in der
    Farbe des Rings, die mit dem Ring zusammenhängen (sonst stört Sand in derselben Farbe)."""
    x0, y0 = max(0, c[0] - 110), max(0, c[1] - 110)
    sub = im[y0:c[1] + 110, x0:c[0] + 110].astype(int)
    cx, cy = c[0] - x0, c[1] - y0
    hh, ww = sub.shape[:2]
    yy, xx = np.mgrid[0:hh, 0:ww]
    d = np.hypot(xx - cx, yy - cy)
    ring = (d > 50) & (d < 58)
    med = np.median(sub[ring], axis=0)
    close = ((np.abs(sub - med).sum(2) < 70) & (d < 100)).astype(np.uint8)
    if not (ring & (close > 0)).any():
        return None
    _, lab = cv2.connectedComponents(close)
    rl = np.bincount(lab[ring & (close > 0)]).argmax()
    sel = (lab == rl) & (d > 66)
    if sel.sum() < 15:
        return None
    ang = np.arctan2(yy[sel] - cy, xx[sel] - cx)
    return float(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean()))


def player_spot(img) -> tuple[int, int]:
    """Mitte des Felds, auf dem die Figur steht: ihre orangen Haare plus 150 px nach unten. An Abzweigungen steht die
    Kamera so, dass die Haare bei etwa (800, 276) sind (Video: 18 von 19 Abzweigungen)."""
    im = cv2.resize(img, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 5) & (hsv[..., 0] <= 16) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 170)).astype(np.uint8)
    m[:110] = 0
    m[760:] = 0
    m[:, :230] = 0
    m[:, 1330:] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _, st, c = cv2.connectedComponentsWithStats(m)
    if n >= 2:
        i = 1 + int(np.argmax(st[1:, 4]))
        if st[i, 4] > 3000:
            return int(c[i][0]) + 5, int(c[i][1]) + 150
    return 805, 426


def goal_dir(img):
    """Richtung zum Ziel (Winkel im Bild) und woher sie kommt: 'fahne' (Zielfahne auf dem Brett zu sehen) oder
    'anzeige' (Kreis mit Fahne am Rand, seine Spitze zeigt zum Ziel). None, wenn keins von beiden zu sehen ist."""
    im = cv2.resize(img, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    m, c, _ = _match(im, _tpl('ziel_anzeige.png'), (1.0,))
    if m > 0.8:
        a = _pointer(im, c)
        if a is not None:
            return a, 'anzeige', c
    m, c, sc = _match(im, _tpl('ziel_fahne.png'), (0.8, 0.9, 1.0, 1.1, 1.2))
    if m > 0.7:
        base = (c[0] - 43 * sc, c[1] + 143 * sc)         # Fuß der Fahnenstange = Zielfeld
        px, py = player_spot(img)
        return float(np.arctan2(base[1] - py, base[0] - px)), 'fahne', (int(base[0]), int(base[1]))
    return None


def merge_arrows(lists):
    """Pfeile aus mehreren Bildern zusammenlegen (sie pulsieren, nicht jeder ist auf jedem Bild zu sehen)."""
    out = []
    for lst in lists:
        for a in lst:
            if all((a[0] - o[0]) ** 2 + (a[1] - o[1]) ** 2 > 60 ** 2 for o in out):
                out.append(a)
    return out


def pick_arrow(img, arrows, g=None, away=True):
    """Der Pfeil, der am weitesten vom Ziel weg zeigt (away=True) oder am ehesten hin. Ohne Ziel: zufällig.
    Wer am Ziel ankommt, beendet den Lauf, und die übrigen Action Points verfallen; die Belohnung gibt es auch so."""
    g = g or goal_dir(img)
    if not g or len(arrows) < 2:
        return random.choice(arrows), None
    px, py = player_spot(img)
    ga = g[0]

    def score(a):
        return np.cos(np.arctan2(a[1] - py, a[0] - px) - ga)
    return (min if away else max)(arrows, key=score), g


def moving(a: np.ndarray, b: np.ndarray) -> float:
    """Anteil (in %) der Bildpunkte im Brett, die sich zwischen zwei Bildern deutlich geändert haben."""
    def g(img):
        im = cv2.resize(img, (800, 450), interpolation=cv2.INTER_AREA)
        x0, y0, x1, y1 = (v // 2 for v in STILL)
        return cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)
    return float((np.abs(g(a) - g(b)) > 25).mean() * 100)


def classify(boxes) -> tuple[str, dict]:
    """Welche Seite ist offen? Gibt (Name, Infos) zurück."""
    ns = [b.n for b in boxes]
    has = lambda w: any(w in n for n in ns)                            # noqa: E731
    info: dict = {}
    m = next((re.search(r'actionpoints(\d+|o)', n) for n in ns if 'actionpoints' in n), None)
    if m:
        info['ap'] = 0 if m.group(1) == 'o' else int(m.group(1))
    m = next((re.search(r'round(\d+)', n) for n in ns if n.startswith('round')), None)
    if m:
        info['round'] = int(m.group(1))
    if has('exercisingcourses'):
        m = next((re.search(r'exercisingleft(\d+)(\d)$', n) for n in ns if 'exercisingleft' in n), None)
        if m:
            info['left'] = int(m.group(1))
            info['max'] = int(m.group(2))
        return 'courses', info
    if find(boxes, 'Claim & Exit') or has('claimexit'):
        return 'finish', info
    if has('taptoclose'):
        return 'obtained', info
    if has('confirmation') and (has('nonegativeeffect') or has('stillwanttouse')):
        return 'noeffect', info             # Jell-O ohne schlechten Effekt: nicht bestätigen, schließen
    if has('stepselection') or has('movementwillbegin'):
        return 'steps', info
    if any(n in ('gotit', 'gotit!') for n in ns) or has('goaltips'):
        return 'tips', info                 # Hinweis am Anfang eines Laufs („Goal Tips“ → „Got it“)
    if has('currentactionpoints') and (has('dontbuy') or has('purchase') or has('shop')):
        return 'shop', info
    if has('owned') and any(n == 'use' for n in ns):
        return 'card', info
    if any(n == 'story' or n.startswith('story') for n in ns):
        return 'story', info
    if 'ap' in info and goal_buttons(boxes):
        return 'goal', info
    if any(n == 'complete' or n == 'event' for n in ns):
        return 'event', info
    if has('movesteps'):
        return 'moving', info
    if 'round' in info or has('currentstatus'):
        return 'board', info
    if has('freetime'):
        return 'freetime', info
    return 'other', info


# Fenster am Zielfeld („RESULT“): links „Claim“ beendet den Lauf sofort (die übrigen Action Points verfallen), rechts
# „Skip“ läuft weiter (Bildschirmfoto vom 15.11.2026). Weitere Wörter, falls sich die Knöpfe einmal ändern.
GOAL_WORDS = ('finish', 'goal', 'reached', 'endexercis', 'endrun', 'finishline', 'result', 'exercisingends',
              'claimingrewards', 'additionalrewards')
GO_ON = ('skip', 'continue', 'keepgoing', 'keepexercis', 'goon', 'carryon', 'notyet', 'keeprunning')
STOP = ('claim', 'endexercis', 'endrun', 'finishnow', 'finishexercis', 'endnow', 'leave', 'quit')


def goal_buttons(boxes):
    """(beenden, weiter) im Fenster am Zielfeld, sonst None. Weiter ist ein Knopf mit „Continue“ o. ä.; sonst der
    rechte von zwei Knöpfen in derselben Zeile, wenn im Fenster von Ziel/Finish die Rede ist."""
    ns = [b.n for b in boxes]
    if any('claimexit' in n or n.startswith('story') for n in ns):
        return None
    btn = [b for b in boxes if 560 < b.cy < 840 and 150 < b.cx < 1450 and 2 <= len(b.n) <= 22
           and not b.n.startswith(('auto', 'round', 'storage')) and sum(ch.isdigit() for ch in b.n) * 2 < len(b.n)]
    pairs = []
    for i, l in enumerate(btn):
        for r in btn[i + 1:]:
            if abs(l.cy - r.cy) < 25:
                l2, r2 = sorted((l, r), key=lambda b: b.cx)
                if l2.cx < 800 < r2.cx:
                    pairs.append((l2, r2))
    talk = any(w in n for n in ns for w in GOAL_WORDS)
    # erst Paare mit bekannten Wörtern („Claim“ / „Skip“), dann (nur wenn vom Ziel die Rede ist) irgendein Paar
    for left, right in pairs:
        if any(w in right.n for w in GO_ON) or any(w in left.n for w in STOP):
            return left, right
        if any(w in left.n for w in GO_ON) or any(w in right.n for w in STOP):
            return right, left
    if talk and pairs:
        return max(pairs, key=lambda p: p[0].cy)          # die Knöpfe stehen unten
    return None


# ---------------------------------------------------------------- Karten an ihrem Symbol erkennen
# Der Bot öffnet eine Karte nur einmal, um ihren Namen zu lesen, und speichert ihr Symbol in bilder/karten/
# (Name_Nummer.png). Danach erkennt er sie am Symbol, auch in späteren Läufen, und öffnet sie nur noch zum Benutzen.
CARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bilder', 'karten')
NEVER_CARDS = ('reverse', 'hitchhiking')            # benutzt der Bot nie
_CARDS = None


def card_library():
    global _CARDS
    if _CARDS is None:
        _CARDS = []
        for p in sorted(glob.glob(os.path.join(CARD_DIR, '*.png'))):
            im = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
            if im is not None:
                _CARDS.append((os.path.basename(p).rsplit('_', 1)[0], im))
    return _CARDS


def learn_card(name, icon):
    lib = card_library()
    if not name or sum(1 for n, _ in lib if n == name) >= 3:
        return
    os.makedirs(CARD_DIR, exist_ok=True)
    k = sum(1 for n, _ in lib if n == name)
    cv2.imwrite(os.path.join(CARD_DIR, f'{name}_{k}.png'), icon)
    lib.append((name, icon))


# ---------------------------------------------------------------- Story-Fragen aus den Spieldaten
# exercising_fragen.json: alle 48 Fragen (de/en) mit der Belohnung für A und B (aus treasureevent, 12.11.2026).
# r = [Belohnung A, Menge A, Belohnung B, Menge B]; 108 = Diamanten, 100 = Gold, 7010000xx = Karten.
DIA, GOLD = 108, 100
ITEM_ORDER = [701000005, 701000007, 701000004, 701000006, 701000008, 701000009, 701000001]  # nützlichste zuerst
_FRAGEN = None


def fragen():
    global _FRAGEN
    if _FRAGEN is None:
        try:
            import json
            with open(os.path.join(HERE, 'exercising_fragen.json'), encoding='utf-8') as f:
                d = json.load(f)
            _FRAGEN = []
            for q in d['q']:
                for lang in ('en', 'de'):
                    if q.get(lang):
                        _FRAGEN.append({'id': q['id'], 'r': q['r'], 'n': norm(q[lang][0]), 'a': q[lang][1],
                                        'b': q[lang][2]})
            _FRAGEN_ITEMS.update(d.get('items', {}))
        except Exception:
            _FRAGEN = []
    return _FRAGEN


_FRAGEN_ITEMS = {}


def find_question(text):
    """Die Frage aus den Spieldaten, die am besten zum gelesenen Text passt (None, wenn unsicher). Verglichen wird
    ohne Leerzeichen, weil die Texterkennung sie oft weglässt („Asmallpuddleblocks...“)."""
    have = norm(text)
    if len(have) < 15:
        return None
    best, bp, second = None, 0.0, 0.0
    for q in fragen():
        sm = difflib.SequenceMatcher(None, have, q['n'], autojunk=False)
        p = sm.ratio()
        if p > bp:
            best, bp, second = q, p, bp
        elif p > second:
            second = p
    return best if best and bp >= 0.6 and bp - second >= 0.08 else None


def reward_rank(item, amount):
    """Je größer, desto lieber: Diamanten vor nützlichen Karten vor Gold."""
    if item == DIA:
        return 3_000_000 + amount
    if item in ITEM_ORDER:
        return 2_000_000 - ITEM_ORDER.index(item)
    if item == GOLD:
        return 1_000_000 + amount // 100
    return 0


def reward_name(item, amount):
    it = _FRAGEN_ITEMS.get(str(item)) or {}
    return f"{amount}× {it.get('en') or item}"


def story_options(boxes):
    """Die beiden Antworten (A links, B rechts) unter einer Story-Frage."""
    st = [b for b in boxes if b.n.startswith('story')]
    if not st:
        return None
    top = st[0].cy
    cand = [b for b in boxes if top + 150 < b.cy < top + 480 and b.n and 120 < b.cx < 1480 and len(b.n) >= 3
            and not b.n.startswith('auto')]
    if not cand:
        return None
    low = max(b.cy for b in cand)                         # die Antworten stehen unter dem Fragetext
    row = [b for b in cand if abs(b.cy - low) < 25]
    left = [b for b in row if b.cx < 800]
    right = [b for b in row if b.cx >= 800]
    if not left or not right:
        return None
    return min(left, key=lambda b: b.cx), max(right, key=lambda b: b.cx)


# ---------------------------------------------------------------- Bot

class Exerciser:
    def __init__(self, dev, reader, log=print, course=4, story='diamant', cards=True, runs=0, debug_dir=None, way='einmal'):
        self.dev, self.reader, self.log = dev, reader, log
        dev.reader = reader
        self.course = max(1, min(4, int(course or 4)))
        self.story = story if story in ('diamant', 'a', 'b', 'zufall') else 'diamant'
        self.cards = cards
        # an Abzweigungen: einmal = erst zum Ziel, dort weiterlaufen, danach weg vom Ziel; meiden; ziel; zufall
        self.way = way if way in ('einmal', 'meiden', 'ziel', 'zufall') else 'einmal'
        self.goal_reached = False
        self.to_goal = self.way != 'zufall'   # Zielrichtung lesen
        self.last_goal = None               # zuletzt gesehene Richtung zum Ziel
        self.runs = runs                    # 0 = bis „Exercising Left“ 0 ist
        self.debug_dir = debug_dir
        self.done = 0
        self.skip_cards: list[np.ndarray] = []   # Karten, die der Bot nicht benutzt (oder leere Plätze)
        self.card_try = None                # (Platz, Ausschnitt) der Karte, die er gerade geöffnet hat
        self.tried = set()                  # Plätze, die er in dieser Runde schon angesehen hat
        self.card_looks = 0                 # so oft hat er in dieser Runde eine Karte geöffnet
        self.known_cards = []               # (Bild, Name) von Karten, die er für später aufhebt
        self.round_skip = []                # Karten, die er nur in dieser Runde nicht will (Sports Drink, Jell-O)
        self.plan_round = None
        self.want_double = False
        self.bad_ahead = False
        self.want_steps = None              # Steps Setter: so viele Schritte wählen
        self.tracker = None                 # Position auf der Strecke aus den Spieldaten (exmap)
        self.entered = None                 # Kurs, den der Bot selbst mit „Enter“ gestartet hat
        self.drain = 0                      # Runden mit Stamina Drain
        self.heavy = 0                      # Runden, in denen ein Wurf 2 Action Points kostet (Hantel)
        self.ap = None
        self.round = None
        self.go_at_zero = False
        self.other = 0
        self.stats = {'go': 0, 'story': 0, 'event': 0, 'shop': 0, 'karten': 0, 'abzweig': 0, 'items': 0}

    # ------------------------------------------------------------ Hilfen
    def snap(self):
        return self.dev.screenshot()

    def shot(self, name):
        if not self.debug_dir:
            return None
        os.makedirs(self.debug_dir, exist_ok=True)
        path = os.path.join(self.debug_dir, time.strftime('%H%M%S_') + name + '.png')
        if self.dev.last is not None:
            cv2.imwrite(path, self.dev.last)
        return path

    def crop(self, img, x, y, r=CARD_BOX):
        h, w = img.shape[:2]
        sx, sy = w / REF_W, h / REF_H
        c = img[int((y - r) * sy):int((y + r) * sy), int((x - r) * sx):int((x + r) * sx)]
        return cv2.resize(c, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)

    def icon(self, img, i, pad=0):
        """Graues Bild vom Symbol der Karte in Platz i (Mitte der Karte, ohne Rand und Hintergrund)."""
        x, y = CARD_SLOTS[i]
        h, w = img.shape[:2]
        sx, sy = w / REF_W, h / REF_H
        r = 36 + pad
        c = img[int((y - 18 - r) * sy):int((y - 18 + r) * sy), int((x - r) * sx):int((x + r) * sx)]
        c = cv2.resize(c, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)

    def card_name(self, img, i):
        """Name der Karte in Platz i nach ihrem Symbol (gelernt aus früheren Läufen, bilder/karten), sonst None."""
        big = self.icon(img, i, pad=8)
        best, name = 0.0, None
        for nm, tpl in card_library():
            r = float(cv2.matchTemplate(big, tpl, cv2.TM_CCOEFF_NORMED).max())
            if r > best:
                best, name = r, nm
        return name if best >= 0.78 else None

    def same_card(self, a, b):
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()) < 12

    def tap(self, x, y, purpose, expect: str | None = None, wait=1.0, allow=()):
        """Tippt erst, nachdem die Stelle gelesen ist: dort darf nichts Verbotenes stehen; mit expect muss der Text
        dort stehen."""
        img = self.snap()
        for (x0, y0, x1, y1) in L.DANGER_TOP:
            if x0 <= x <= x1 and y0 <= y <= y1:
                raise SafetyStop(T('Abgebrochen: „{purpose}“ läge auf einem gesperrten Knopf ({x}, {y}).',
                                   purpose=purpose, x=round(x), y=round(y)))
        near = self.reader.read(img, (x - 160, y - 45, x + 160, y + 45))
        for b in near:
            if b.x0 - 10 <= x <= b.x1 + 10 and b.y0 - 10 <= y <= b.y1 + 10 and not tap_text_ok(b.text, allow):
                raise SafetyStop(T('Abgebrochen: An der Stelle für „{purpose}“ steht „{text}“.', purpose=purpose, text=b.text))
        if expect and not any(norm(expect) in b.n for b in near):
            return False
        self.dev.tap(x, y, purpose, wait=wait, check=False)
        return True

    def settle(self, timeout=6.0):
        """Wartet, bis das Brett still steht (Figur läuft nicht mehr, Kamera fährt nicht mehr)."""
        t0 = time.time()
        a = self.snap()
        calm = 0
        while time.time() - t0 < timeout:
            time.sleep(0.35)
            b = self.snap()
            calm = calm + 1 if moving(a, b) < 2.5 else 0
            if calm >= 2:
                return b
            a = b
        return a

    # ------------------------------------------------------------ Seiten
    def on_courses(self, boxes, info):
        cont = [b for b in boxes if b.n.startswith('contin')]
        if cont:
            self.log(T('  Lauf läuft noch: „Continue“'))
            self.tap(cont[0].cx, cont[0].cy, 'Continue', expect='contin', wait=2.5)
            return True
        left = info.get('left')
        if left is None:
            self.log(T('  „Exercising Left“ nicht lesbar; ich höre auf.'))
            return False
        if left <= 0:
            self.log(T('  Exercising Left: 0. Für heute fertig.'))
            return False
        if self.runs and self.done >= self.runs:
            self.log(T('  {n} Läufe geschafft, wie gewünscht.', n=self.done))
            return False
        enter = sorted([b for b in boxes if b.n == 'enter'], key=lambda b: b.cx)
        if len(enter) < 4:
            self.log(T('  Die „Enter“-Knöpfe sind nicht alle zu sehen; ich höre auf.'))
            self.shot('kurse')
            return False
        b = enter[self.course - 1]
        # grau = gesperrt (dann läuft ein anderer Kurs, und es gäbe „Continue“)
        img = self.dev.last
        h, w = img.shape[:2]
        px = img[int((b.cy) * h / REF_H), int((b.x0 - 20) * w / REF_W)]
        if int(px[0]) - int(px[2]) < 40:
            self.log(T('  „Enter“ bei {course} ist grau; ich höre auf.', course=COURSE_NAMES[self.course - 1]))
            self.shot('kurse_grau')
            return False
        self.log(T('Starte {course} (Exercising Left: {left}/{max}).', course=COURSE_NAMES[self.course - 1], left=left,
                   max=info.get('max', '?')))
        self.tap(b.cx, b.cy, 'Enter', expect='enter', wait=3.0)
        self.round = None
        self.skip_cards = []
        self.go_at_zero = False
        self.last_goal = None
        self.goal_reached = False
        self.drain = 0
        self.heavy = 0
        self.tracker = None
        self.entered = self.course
        self.known_cards = []
        return True

    def on_board(self, img, info):
        # neue Runde nur bei einer gelesenen, anderen Zahl (sonst sah der Bot bei jedem unlesbaren „Round“ eine neue
        # Runde und sah sich die Karten immer wieder an, ohne zu würfeln: Lauf vom 15.11.2026)
        r = info.get('round')
        if r is not None and r != self.round:
            self.round = r
            self.card_looks = 0
            self.card_try = None
            self.tried = set()
            self.round_skip = []
            if self.drain > 0:
                self.drain -= 1
            if self.heavy > 0:
                self.heavy -= 1
        img = self.settle()
        if classify(self.reader.read(img))[0] != 'board':
            return                          # inzwischen ist ein Fenster aufgegangen (Story, Shop ...)
        self.see_map(img)
        arrows = yellow_arrows(img)
        if arrows:
            # die Pfeile pulsieren und die Zielanzeige blinkt: ein paar Bilder ansehen
            lists, goal = [arrows], goal_dir(img) if self.to_goal else None
            for _ in range(4):
                time.sleep(0.25)
                im2 = self.snap()
                lists.append(yellow_arrows(im2))
                if self.to_goal and not goal:
                    goal = goal_dir(im2)
            arrows = merge_arrows(lists)
            if goal:
                self.last_goal = goal
            elif self.last_goal:
                goal = (self.last_goal[0], self.last_goal[1] + T(', von vorhin'), None)
            spot = player_spot(img)
            if self.way == 'zufall':
                (x, y) = random.choice(arrows)
                self.log(T('  Abzweigung: {n} Wege, nehme ({x}, {y})', n=len(arrows), x=x, y=y))
            else:
                steps = XB.read_dial(self.reader, img)
                w = 0.0
                if self.way in ('ziel', 'einmal') and not self.goal_reached:
                    ap = info.get('ap')
                    # je weniger Action Points, desto wichtiger der Weg zum Ziel (20 Diamanten)
                    # erst zum Ziel (Nutzer: vom Start aus in Richtung der Fahne): klar stärker als ein Feld Unterschied
                    w = 4.0 if ap is None or ap > 8 else 6.0 if ap > 4 else 8.0
                elif self.way == 'meiden':
                    w = -3.0
                rated = None
                if self.map_ok():
                    t = self.tracker
                    rated, seen = [], []
                    for a in arrows:
                        d = XB.dir_of(spot, a)
                        n = t.neighbor_for(d)
                        if n is None or n in seen:
                            continue
                        seen.append(n)
                        bv = t.branch_value(n, steps, self.goal_reached, goal_weight=w if t.goal else 0.0)
                        if bv is None:
                            continue
                        v = bv[0]
                        if not t.goal and goal and w:       # Ziel noch nicht gesehen: nach der Fahnen-Anzeige
                            v += w * float(np.cos(np.arctan2(d[1], d[0]) - goal[0]))
                        rated.append((v, a, bv[2]))
                    rated.sort(key=lambda r: -r[0])
                    if not rated:
                        rated = None
                if rated is None:
                    rated = XB.rate_arrows(img, spot, arrows, steps=steps, after_goal=self.goal_reached,
                                           goal_angle=goal[0] if goal else None, goal_weight=w,
                                           goal_visible=bool(goal and goal[1] == 'fahne'))
                sc, (x, y), best = rated[0]
                for r_sc, r_a, r_p in rated:
                    self.log(T('    Weg ({x}, {y}): Wert {v:.1f}  {tiles}', x=r_a[0], y=r_a[1], v=r_sc,
                               tiles=' '.join(k for k in r_p[:6] if k != 'unsichtbar') or '?'))
                self.log(T('  Abzweigung: {n} Wege, Würfe {steps}, nehme ({x}, {y}){goal}', n=len(rated),
                           steps='/'.join(map(str, steps)), x=x, y=y,
                           goal=T(' · Ziel bei {deg}° zählt {w:+g}', deg=round(np.degrees(goal[0])), w=w) if goal and w else ''))
            self.stats['abzweig'] += 1
            self.tap(x, y, 'Weg wählen', wait=1.2)
            return
        if self.to_goal:
            g = goal_dir(img)
            if g:
                self.last_goal = g
        if self.cards:
            if self.plan_round != self.round:
                self.plan_round = self.round
                try:
                    self.want_double, self.bad_ahead, why = XB.card_plan(img, player_spot(img), self.goal_reached)
                except Exception as e:  # nur für die Karten, darf den Lauf nicht stoppen
                    self.want_double, self.bad_ahead, why = False, False, str(e)
                self.ap = info.get('ap')
                self.want_steps = None
                if self.map_ok():
                    opts = self.tracker.landing_options(after_goal=self.goal_reached)
                    if opts:
                        s_best = max(opts, key=lambda k: opts[k])
                        rnd = sum(opts.get(k, 0.0) for k in (1, 2, 3)) / 3
                        if opts[s_best] >= XB.GOOD and opts[s_best] - rnd >= 2.5:
                            self.want_steps = s_best
                        why += T(', Landung 1/2/3: {o}', o='/'.join(f"{opts.get(k, 0):g}" for k in (1, 2, 3)))
                self.log(T('  Felder voraus: {why} → Sports Drink {yn}, Energy Candy {yn2}', why=why,
                           yn=T('ja') if self.want_double else T('nein'), yn2=T('ja') if self.want_candy() else T('nein')))
                if self.want_steps:
                    self.log(T('  Steps Setter: {n} Schritte wären gut', n=self.want_steps))
            if self.try_card(img):
                return
        if info.get('ap') == 0 and self.go_at_zero:
            # mit 0 Action Points geht noch ein letzter Zug; danach kommt FINISH. Nicht noch einmal würfeln.
            self.other += 1
            time.sleep(1.0)
            return
        if info.get('ap') == 0:
            self.go_at_zero = True
        self.stats['go'] += 1
        self.log(T('  Runde {r}, Action Points {ap}: GO!', r=info.get('round', '?'), ap=info.get('ap', '?')))
        self.tap(*GO, 'GO', wait=2.0)

    def see_map(self, img):
        """Position auf der Strecke bestimmen und die sichtbaren Symbole merken."""
        try:
            g = goal_dir(img)
            obs, _ = XM.observe(img, player_spot(img), goal_visible=bool(g and g[1] == 'fahne'))
            if self.tracker is None:
                if self.entered:
                    course = XM.Course(XM.MAP_IDS[self.entered])
                else:
                    mid, sc, _ = XM.guess_course(obs)
                    course = XM.Course(mid)
                    self.log(T('  Strecke erkannt: {name}', name=COURSE_NAMES[mid - 1001]))
                self.tracker = XM.Tracker(course)
            t = self.tracker
            if t.update(obs, self.log):
                if g and g[1] == 'fahne' and g[2] is not None:
                    t.set_goal_from_flag(g[2], player_spot(img))
                if t.goal and t.pos == t.goal and not self.goal_reached:
                    self.goal_reached = True
                    self.log(T('  Am Ziel angekommen.'))
                d = ''
                if t.goal:
                    dist = t.course.dist_from(t.pos).get(t.goal)
                    d = T(', Ziel {g} ({n} Felder)', g=t.goal, n=dist)
                self.log(T('    Karte: Feld {pos}{d}, {k} Symbole bekannt', pos=t.pos, d=d, k=len(t.kinds)))
        except Exception as e:      # die Karte ist eine Hilfe; ohne sie spielt der Bot wie vorher
            self.log(T('    (Karte: {err})', err=e))

    def map_ok(self):
        return self.tracker is not None and self.tracker.pos is not None

    def card_wanted(self, n):
        """Würde der Bot die Karte mit diesem Namen (ohne Leerzeichen, klein) jetzt benutzen?"""
        if 'stepssetter' in n:
            return bool(self.want_steps)
        if 'energycandy' in n:
            return self.want_candy()
        if 'sportsdrink' in n:
            return self.want_double or (self.ap is not None and self.ap <= 2)
        if 'vitaminc' in n or 'jello' in n:
            return (self.drain > 0 and not self.goal_reached) or self.heavy > 0
        return True

    def want_candy(self):
        """Energy Candy (8 Schritte ohne Action Points): wenn voraus viele schlechte Felder liegen, wenn die Action
        Points fürs Ziel knapp werden (Ziel noch nicht erreicht, höchstens 5 AP) oder ganz am Ende (sonst verfällt sie)."""
        ap = self.ap
        if self.bad_ahead:
            return True
        if ap is not None and ap <= 5 and not self.goal_reached and self.way in ('einmal', 'ziel'):
            return True
        return ap is not None and ap <= 1

    def try_card(self, img) -> bool:
        """Öffnet die nächste Karte, die der Bot in dieser Runde noch nicht angesehen hat (höchstens 6 je Runde)."""
        if self.card_looks >= 6:
            return False
        if self.card_try is not None:
            # eben getippt, aber kein Fenster: der Platz ist leer, die Plätze rechts davon auch
            self.tried.update(range(self.card_try[0], len(CARD_SLOTS)))
            self.card_try = None
        for i, (x, y) in enumerate(CARD_SLOTS):
            if i in self.tried:
                continue
            self.tried.add(i)
            c = self.crop(img, x, y)
            if any(self.same_card(c, s) for s in self.skip_cards + self.round_skip):
                continue
            known = self.card_name(img, i) or next((nm for kc, nm in self.known_cards if self.same_card(c, kc)), None)
            if known and (known in NEVER_CARDS or not self.card_wanted(known)):
                continue                    # Karte am Symbol erkannt und jetzt nicht nötig: nicht öffnen
            self.card_try = (i, c, self.icon(img, i))
            self.card_looks += 1
            self.tap(x, y, 'Karte ansehen', wait=1.0)
            return True
        return False

    def on_card(self, boxes):
        title = ''
        own = [b for b in boxes if b.n.startswith('owned')]
        if own:
            above = [b for b in boxes if b.cy < own[0].cy - 10 and abs(b.x0 - own[0].x0) < 40]
            if above:
                title = max(above, key=lambda b: b.cy).text
        text = ' '.join(b.text for b in boxes if own and own[0].cy - 60 < b.cy < own[0].cy + 120)
        n = norm(title)
        if n and self.card_try and len(self.card_try) > 2:
            learn_card(n, self.card_try[2])  # Symbol merken: nächstes Mal ohne Öffnen erkennen
        later = False                      # nicht jetzt, aber vielleicht in einer späteren Runde
        steps_card = 'stepssetter' in n or 'choosethenumber' in norm(text)
        if steps_card:
            good = bool(self.want_steps)
            later = not good
        elif 'energycandy' in n:
            good = self.want_candy()
            later = not good
        elif 'sportsdrink' in n:
            good = self.want_double or (self.ap is not None and self.ap <= 2)
            later = not good
        elif 'vitaminc' in n or 'jello' in n:
            good = (self.drain > 0 and not self.goal_reached) or self.heavy > 0
            later = not good
        else:
            good = n in GOOD_CARDS or any(p in norm(text) for p in GOOD_PARTS)
        slot = self.card_try[0] if self.card_try else None
        use = next((b for b in boxes if b.n == 'use'), None)
        if good and use:
            self.log(T('  Karte „{name}“: benutzen', name=title))
            self.stats['karten'] += 1
            self.tap(use.cx, use.cy, 'Use', expect='use', wait=1.5)
            self.card_try = None
            self.tried = set()              # die Karten rücken nach links
            if steps_card:
                self.choose_steps(self.want_steps)
                self.want_steps = None
            if 'vitaminc' in n or 'jello' in n:
                self.drain = self.heavy = 0     # der schlechte Effekt ist weg
            return
        self.log(T('  Karte „{name}“: bleibt liegen', name=title or '?'))
        if self.card_try:
            (self.round_skip if later else self.skip_cards).append(self.card_try[1])
            if later:
                self.known_cards.append((self.card_try[1], n))
        self.card_try = None
        # schließen: dieselbe Karte noch einmal antippen, sonst daneben aufs Brett
        if slot is not None:
            self.tap(*CARD_SLOTS[slot], 'Karten-Fenster schließen', wait=0.8)
        if classify(self.reader.read(self.snap()))[0] == 'card':
            self.tap(800, 690, 'Karten-Fenster schließen', wait=0.8)

    def on_goal(self, boxes):
        """Am Zielfeld: weiterlaufen, damit die übrigen Action Points nicht verfallen."""
        bt = goal_buttons(boxes)
        if not bt:
            self.other += 1
            return
        stop, go = bt
        txt = ' '.join(b.text for b in boxes if 120 < b.cy < 560 and 150 < b.cx < 1450)
        self.log(T('  ZIEL erreicht: {t} | Knöpfe „{a}“ / „{b}“ → „{b}“ (weiterlaufen)', t=txt[:100], a=stop.text, b=go.text))
        self.shot('ziel')
        self.goal_reached = True
        self.tap(go.cx, go.cy, 'Weiterlaufen', wait=1.5)

    STEP_BOXES = {1: (406, 393), 2: (800, 393), 3: (1201, 393)}   # Fenster „Step Selection“ (Screenshot 15.11.2026)
    STEP_OK = (792, 673)

    def choose_steps(self, n):
        """Fenster „Step Selection“ nach dem Steps Setter: Karte 1 (links), 2 (Mitte) oder 3 (rechts) antippen, dann
        „OK“. Die großen Zahlen liest die Texterkennung nicht sicher, darum feste Stellen; „OK“ wird gelesen."""
        for _ in range(4):
            boxes = self.reader.read(self.snap())
            if classify(boxes)[0] == 'steps':
                break
            time.sleep(0.6)
        else:
            self.shot('steps_setter')
            self.log(T('  Steps Setter: das Fenster „Step Selection“ kam nicht (Bildschirmfoto gespeichert).'))
            return
        n = n if n in (1, 2, 3) else 2
        self.tap(*self.STEP_BOXES[n], 'Schritte wählen', wait=0.8)
        ok = [b for b in self.reader.read(self.snap()) if b.n == 'ok']
        x, y = (ok[0].cx, ok[0].cy) if ok else self.STEP_OK
        self.tap(x, y, 'Bestätigen', expect='ok', wait=1.5)
        self.log(T('  Steps Setter: {n} Schritte gewählt', n=n))

    def on_story(self, boxes):
        opt = story_options(boxes)
        if not opt:
            # Ergebnis der Story-Frage (Frage, Antworttext und „Complete“): weiter mit „Complete“ (15.11.2026)
            c = [b for b in boxes if b.n.startswith('complet') or b.n in ('comnete', 'compete')]
            if c:
                self.tap(c[0].cx, c[0].cy, 'Complete', wait=1.5)
                return
            self.other += 1
            return
        q = ' '.join(x.text for x in boxes if x.n.startswith('story') is False and opt[0].cy - 260 < x.cy < opt[0].cy - 60)
        why = ''
        if self.story == 'diamant':
            fq = find_question(q)
            if fq:
                r = fq['r']
                pick = 'a' if reward_rank(r[0], r[1]) >= reward_rank(r[2], r[3]) else 'b'
                # A und B am Text erkennen (falls das Spiel sie einmal anders herum zeigt)
                want = norm(fq[pick])
                sa, sb = (difflib.SequenceMatcher(None, o.n, want).ratio() for o in opt)
                b = opt[0] if sa >= sb else opt[1]
                why = T(' (A: {a}, B: {b})', a=reward_name(r[0], r[1]), b=reward_name(r[2], r[3]))
            else:
                b = opt[0]
                why = T(' (Frage nicht in den Spieldaten gefunden, nehme A)')
        else:
            pick = self.story if self.story != 'zufall' else random.choice('ab')
            b = opt[0] if pick == 'a' else opt[1]
        self.log(T('  Story: {q} → {a}{why}', q=q[:90], a=b.text, why=why))
        self.stats['story'] += 1
        # Story-Antworten wie „Buy a bottle of water“ kosten nichts (Spieldaten: die Belohnung kommt dazu); nur das
        # Wort „buy“ ist hier erlaubt, Gems, Diamonds, Recharge usw. stoppen weiter
        self.tap(b.cx, b.cy, 'Antwort', wait=1.5, allow=('buy', 'purchas', 'store', 'shop'))

    def on_event(self, boxes):
        ev = next((b for b in boxes if b.n == 'event'), None)
        c = [b for b in boxes if b.n.startswith('complet')]
        if not c and ev:
            # die Texterkennung liest den Knopf manchmal falsch („ComNete“): der unterste Text in der Mitte
            c = [b for b in boxes if b.cy > ev.cy + 150 and 650 < b.cx < 950 and b.cy < 840 and not b.n.startswith('auto')]
            c = sorted(c, key=lambda b: -b.cy)[:1]
        txt = ' '.join(b.text for b in boxes if ev and ev.cy + 20 < b.cy < ev.cy + 260 and b not in c)
        self.log(T('  Event: {t}', t=txt[:100]))
        nt = norm(txt)
        if 'stamina' in nt:
            self.drain = 3
        elif 'shortcut' in nt or 'supply' in nt:
            pass
        elif ('actionpoint' in nt and ('2' in nt or 'two' in nt or 'double' in nt or 'twice' in nt)) \
                or any(w in nt for w in ('random', 'direction', 'dizzy', 'confus', 'cannot', 'cant', 'consum')):
            self.heavy = 4                  # Hantel (4 Runden 2 AP je Wurf) oder Wirbel (zufällige Richtung): Jell-O hilft
        if not c:
            self.other += 1
            return
        self.stats['event'] += 1
        self.tap(c[0].cx, c[0].cy, 'Complete', wait=1.5)

    def on_shop(self, boxes):
        d = [b for b in boxes if 'dontbuy' in b.n or 'donotbuy' in b.n]
        if not d:
            # ein Teil ist ausgewählt („Purchase“): der Bot wählt nie etwas aus. Abbrechen statt raten.
            self.shot('shop')
            raise SafetyStop(T('Im Shop ist etwas ausgewählt („Purchase“). Bitte selbst „Don\'t Buy“ wählen und neu starten.'))
        self.stats['shop'] += 1
        self.log(T('  Shop: Don\'t Buy'))
        self.tap(d[0].cx, d[0].cy, "Don't Buy", expect='buy', wait=1.2)

    # ------------------------------------------------------------ Lauf
    def run(self):
        self.log(T('Exercising-Bot: Kurs {course}, Story-Antwort {story}, Karten {cards}, Läufe {runs}, Abzweigungen {way}.',
                   course=COURSE_NAMES[self.course - 1], story={'diamant': T('nach Belohnung'), 'zufall': T('zufällig')}.get(self.story, self.story.upper()),
                   cards=T('ja') if self.cards else T('nein'), runs=self.runs or T('alle übrigen'),
                   way={'meiden': T('weg vom Ziel'), 'ziel': T('Richtung Ziel'),
                        'einmal': T('erst zum Ziel, dort weiter, dann weg')}.get(self.way, T('zufällig'))))
        last_state = None
        while True:
            img = self.snap()
            boxes = self.reader.read(img)
            state, info = classify(boxes)
            prev = last_state
            if state != last_state and state not in ('board', 'moving'):
                self.other = 0
            last_state = state
            if state == 'courses':
                if not self.on_courses(boxes, info):
                    break
            elif state == 'finish':
                if prev != 'finish':
                    self.done += 1
                self.log(T('FINISH: Lauf {n} geschafft. „Claim & Exit“', n=self.done))
                self.tap(*next(((b.cx, b.cy) for b in boxes if 'claim' in b.n), (800, 814)), 'Claim & Exit',
                         expect='claim', wait=3.0)
            elif state == 'obtained':
                self.stats['items'] += 1
                b = next(b for b in boxes if 'taptoclose' in b.n)
                self.tap(b.cx, b.cy, 'Tap to close', wait=1.0)
            elif state == 'shop':
                self.on_shop(boxes)
            elif state == 'card':
                self.on_card(boxes)
            elif state == 'story':
                self.on_story(boxes)
            elif state == 'goal':
                self.on_goal(boxes)
            elif state == 'noeffect':
                self.drain = self.heavy = 0
                t = next((b for b in boxes if b.n.startswith('confirmation')), None)
                self.log(T('  Kein schlechter Effekt mehr: Vitamin C Jell-O nicht benutzen, Fenster schließen'))
                # das X oben rechts im Fenster (Screenshot 15.11.2026: Fenster 1600er-Bild x 535-1105, Titel bei y 300)
                self.tap(1082, t.cy if t else 300, 'Fenster schließen', wait=1.2)
            elif state == 'tips':
                b = next((b for b in boxes if b.n.startswith('gotit')), None)
                if b:
                    self.log(T('  Hinweis „{t}“ weggetippt', t='Goal Tips'))
                    self.tap(b.cx, b.cy, 'Got it', expect='gotit', wait=1.5)
                else:
                    self.other += 1
                    time.sleep(1.0)
            elif state == 'steps':
                n = self.want_steps
                if not n and self.map_ok():
                    opts = self.tracker.landing_options(after_goal=self.goal_reached)
                    n = max(opts, key=lambda k: opts[k]) if opts else 2
                self.choose_steps(n or 2)
                self.want_steps = None
            elif state == 'event':
                self.on_event(boxes)
            elif state == 'moving':
                time.sleep(0.8)
            elif state == 'board':
                if not (info.get('ap') == 0 and self.go_at_zero):
                    self.other = 0
                self.on_board(img, info)
            elif state == 'freetime':
                ex = [b for b in boxes if b.n.startswith('exercising')]
                if ex:
                    self.tap(ex[0].cx, ex[0].cy, 'Exercising', expect='exercising', wait=2.5)
                else:
                    self.other += 1
                    time.sleep(1.0)
            else:
                bad = [b.text for b in boxes if not tap_text_ok(b.text)]
                if bad:
                    self.shot('fenster_mit_kauf')
                    raise SafetyStop(T('Ein Fenster zum Kaufen ist offen („{text}“). Ich tippe dort nichts.', text=bad[0]))
                self.other += 1
                time.sleep(1.0)
            if self.other >= 15:
                path = self.shot('unbekannt')
                self.log(T('Diese Seite kenne ich nicht (Bildschirmfoto: {path}). Ich höre auf.', path=path))
                break
        s = self.stats
        self.log(T('Fertig: {n} Läufe. GO {go}×, Abzweigungen {ab}, Story {st}, Events {ev}, Shop {sh}, Karten benutzt {k}, '
                   'Belohnungen {it}.', n=self.done, go=s['go'], ab=s['abzweig'], st=s['story'], ev=s['event'], sh=s['shop'],
                   k=s['karten'], it=s['items']))
        return self.done
