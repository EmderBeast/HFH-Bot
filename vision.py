"""Texterkennung und Auswertung der Spielbilder (HAIKYU!! FLY HIGH, Global, englische Oberfläche).

Alle Koordinaten sind Referenz-Koordinaten in einem 1600 x 900 großen Bild (16:9). Das Gerät rechnet
sie auf die echte Auflösung des Emulators um.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from texts import T

REF_W, REF_H = 1600, 900

# ---------------------------------------------------------------- Namen der Werte und Sets

# Spieltext (ohne Leerzeichen, klein) -> Schlüssel wie im Simulator
STAT_WORDS = {
    'quickattack': 'Quick', 'quickatk': 'Quick',
    'powerattack': 'Power', 'poweratk': 'Power',
    'set': 'Set', 'serve': 'Serve', 'receive': 'Receive', 'block': 'Block', 'save': 'Save',
    'awareness': 'Awareness', 'strength': 'Strength', 'reflex': 'Reflex', 'spirit': 'Spirit',
    'attacktechnique': 'AtkTech', 'defensetechnique': 'DefTech',
}
BASIC = ['Power', 'Quick', 'Set', 'Serve', 'Receive', 'Block', 'Save']
SECONDARY = ['Awareness', 'Reflex', 'Strength', 'Spirit', 'AtkTech', 'DefTech']
# Wert eines Nebenwert-Wurfs (wie im Simulator, HFH.POT_SUB)
SUB_BASE = {'flat': 24.0, 'pct': 2.0, 'Awareness': 1.6, 'Reflex': 1.6, 'Strength': 3.2, 'Spirit': 3.2,
            'AtkTech': 1.2, 'DefTech': 1.2}
# Hauptwert auf +12 (HFH.POT_MAINVAL); auf Stufe L: max * (L + 3) / 15
MAIN_MAX = {'flat': 300.0, 'pct': 35.0, 'Awareness': 20.0, 'Reflex': 20.0, 'Strength': 40.0, 'Spirit': 40.0,
            'AtkTech': 15.0, 'DefTech': 15.0}

POT_SETS = ['Status EnH.', 'Morale Surge', 'Power Vibe', 'Power Rise', 'Precise Serve', 'Precise Set',
            'Setter Dump', 'Rapid Quick Attack', 'Sharp Sense', 'Awareness EnH.', 'Precise Block',
            'Block Movement', 'Assist Receive', 'Supreme Receive', 'Forceful Guard']
SLOTS = ['I', 'II', 'III', 'IV', 'V', 'VI']
FLAT_SLOTS = {'I', 'III', 'V'}
FIXED_MAIN = {'III': 'Serve', 'V': 'Receive'}
SET_MAIN = {'Status EnH.': 'Power', 'Morale Surge': 'Power', 'Power Vibe': 'Power', 'Power Rise': 'Power',
            'Precise Serve': 'Set', 'Precise Set': 'Set', 'Rapid Quick Attack': 'Quick', 'Sharp Sense': 'Quick',
            'Block Movement': 'Block', 'Forceful Guard': 'Block', 'Assist Receive': 'Receive',
            'Supreme Receive': 'Receive', 'Precise Block': 'Block', 'Setter Dump': 'Set', 'Awareness EnH.': 'Quick'}
# Name im Filter des Spiels, falls anders als im Titel eines Teils
SET_FILTER_NAME = {'Status EnH.': 'Status Enhancement', 'Awareness EnH.': 'Awareness Enhancement'}


def norm(s: str) -> str:
    """Kleinbuchstaben, nur Buchstaben und Ziffern."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def stat_from_text(t: str, cutoff: float = 0.78) -> str | None:
    n = norm(t)
    if not n:
        return None
    if n in STAT_WORDS:
        return STAT_WORDS[n]
    m = difflib.get_close_matches(n, STAT_WORDS.keys(), n=1, cutoff=cutoff)
    return STAT_WORDS[m[0]] if m else None


_SET_NORM = {norm(s): s for s in POT_SETS}
_SET_NORM.update({norm(v): k for k, v in SET_FILTER_NAME.items()})


def set_from_title(t: str) -> str | None:
    """'PowerVibeIll' -> 'Power Vibe'. Der Titel endet mit der Platznummer, die hier egal ist."""
    n = norm(t)
    best, score = None, 0.0
    for key, name in _SET_NORM.items():
        head = n[:len(key)]
        r = difflib.SequenceMatcher(None, head, key).ratio()
        if r > score:
            best, score = name, r
    return best if score >= 0.8 else None


def set_from_text(t: str) -> str | None:
    """Ein ganzer Set-Name, z. B. im Filter ('FORCEFUL GUARD', 'Status Enhancement')."""
    n = norm(t)
    if n in _SET_NORM:
        return _SET_NORM[n]
    m = difflib.get_close_matches(n, _SET_NORM.keys(), n=1, cutoff=0.8)
    return _SET_NORM[m[0]] if m else None


def sub_key(stat: str, pct: bool) -> str:
    if stat in SECONDARY:
        return stat
    return stat + ('Pct' if pct else 'Flat')


def sub_base(key: str) -> float:
    if key in SUB_BASE:
        return SUB_BASE[key]
    return SUB_BASE['pct'] if key.endswith('Pct') else SUB_BASE['flat']


def main_value(slot: str, stat: str, lv: int) -> float:
    lv = max(0, min(12, int(lv)))
    if slot in FLAT_SLOTS:
        mx = MAIN_MAX['flat']
    else:
        mx = MAIN_MAX.get(stat, MAIN_MAX['pct'])
    return mx * (lv + 3) / 15


def main_stat_of(slot: str, piece_set: str, main: str | None) -> str:
    if slot == 'I':
        return SET_MAIN.get(piece_set, 'Power')
    return FIXED_MAIN.get(slot) or main or ''


# ---------------------------------------------------------------- Zahlen

def value_candidates(text: str) -> list[float]:
    """Mögliche Zahlen in einem Wert-Text. Ein verlorenes Komma ('+96%' statt '+9,6 %') gibt mehrere Kandidaten."""
    s = text.replace('O', '0').replace('o', '0')
    has_sep = bool(re.search(r'\d[.,]\d', s))
    digits = re.sub(r'[^0-9.,]', '', s).strip('.,')
    if not digits:
        return []
    if has_sep:
        try:
            return [float(digits.replace(',', '.'))]
        except ValueError:
            digits = re.sub(r'[.,]', '', digits)
    try:
        n = int(re.sub(r'[.,]', '', digits))
    except ValueError:
        return []
    return [float(n), n / 10, n / 100]


def first_value(text: str) -> str:
    """Der erste Wert einer Wert-Spalte. In der Zeile eines Nebenwerts landet manchmal ein fremder Text von der
    Seite dahinter (gesehen: '+4% +12', das '+12' gehört nicht zum Nebenwert); dann zählt nur der Wert direkt
    hinter dem Namen. Ein Text mit nur einem Wert bleibt, wie er ist."""
    parts = re.findall(r'[+]?\s*[0-9Oo][0-9Oo.,]*(?:\s*%)?', text)
    if len(parts) >= 2 and text.count('+') >= 2:
        return parts[0].strip()
    return text


def rolls_of(key: str, text: str) -> int | None:
    """Zahl der Würfe eines Nebenwerts; None, wenn kein Kandidat auf dem Wurf-Raster liegt."""
    base = sub_base(key)
    for v in value_candidates(text):
        r = v / base
        if 0.5 < r < 6.5 and abs(r - round(r)) < 0.03:
            return int(round(r))
    return None


def level_from_main(slot: str, stat: str, text: str) -> int | None:
    for v in value_candidates(text):
        for lv in range(12, -1, -1):
            exp = main_value(slot, stat, lv)
            # das Spiel schneidet nach zwei Nachkommastellen ab (18,66 statt 18,67)
            if abs(v - exp) < 0.02 or abs(v - np.floor(exp * 100) / 100) < 0.005:
                return lv
    return None


# ---------------------------------------------------------------- Texterkennung

@dataclass
class Box:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    score: float = 1.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def n(self) -> str:
        return norm(self.text)

    def __repr__(self) -> str:  # kurz fürs Protokoll
        return f'<{self.text!r} @{self.cx:.0f},{self.cy:.0f}>'


class Reader:
    """Texterkennung mit RapidOCR (läuft offline, ohne Grafikkarte)."""

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.eng = RapidOCR()

    def read(self, img: np.ndarray, region: tuple[float, float, float, float] | None = None,
             upscale: float = 1.0) -> list[Box]:
        """img: Bild in beliebiger 16:9-Auflösung. region in Referenz-Koordinaten. Ergebnis in Referenz-Koordinaten."""
        h, w = img.shape[:2]
        sx, sy = w / REF_W, h / REF_H
        ox = oy = 0
        if region:
            x0, y0, x1, y1 = region
            ox, oy = int(max(0, x0 * sx)), int(max(0, y0 * sy))
            img = img[oy:int(min(h, y1 * sy)), ox:int(min(w, x1 * sx))]
        if img.size == 0:
            return []
        f = 1.0
        if upscale != 1.0:
            # große Ausschnitte nur so weit vergrößern, dass die längere Seite höchstens 1800 Pixel hat (1.15: beim
            # vergrößerten Lesen des ganzen Inventar-Fensters ging der Texterkennung der Speicher aus)
            upscale = min(upscale, max(1.0, 1800 / max(img.shape[:2])))
        if upscale != 1.0:
            img = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
            f = upscale
        # Sehr flache Ausschnitte liest RapidOCR als eine einzige Zeile (ohne Texterkennung der Kästen).
        # Darum oben und unten auffüllen, bis das Seitenverhältnis höchstens 4 : 1 ist.
        ch, cw = img.shape[:2]
        pad = 0
        if cw / max(1, ch) > 4 or ch < 48:
            pad = max(int(cw / 4) - ch, 48 - ch, 0) // 2 + 1
            img = cv2.copyMakeBorder(img, pad, pad, 0, 0, cv2.BORDER_REPLICATE)
        # Kleine Ausschnitte vergrößerte die Texterkennung vor der Suche nach Textkästen auf 736 Pixel Höhe: 0,5 bis
        # 0,7 s für einen Streifen, den sie in Originalgröße in 0,01 bis 0,08 s liest, mit demselben Ergebnis
        # (Messung 11.11.2026). Darum bis 400 Pixel Höhe ohne Vergrößern (1.15).
        try:
            hh = img.shape[0]
            self.eng.text_detector.preprocess_op[0].limit_side_len = 736 if hh >= 400 else max(96, hh)
        except Exception:
            pass
        try:
            res, _ = self.eng(img)
        except Exception:
            # Die Texterkennung selbst ist gescheitert (Scan vom 11.11.2026: „ONNXRuntime inference failed“ beim
            # vergrößerten Lesen, der ganze Teil des Scans ging verloren). Einmal in Originalgröße versuchen, sonst gilt
            # der Ausschnitt als leer; der Aufrufer liest dann noch einmal oder meldet es (1.15).
            try:
                if f != 1.0:
                    img = cv2.resize(img, None, fx=1 / f, fy=1 / f, interpolation=cv2.INTER_AREA)
                    pad = int(round(pad / f))
                    f = 1.0
                res, _ = self.eng(img)
            except Exception:
                res = None
        out = []
        for box, txt, sc in (res or []):
            xs = [p[0] for p in box]
            ys = [p[1] - pad for p in box]
            out.append(Box(str(txt), (min(xs) / f + ox) / sx, (min(ys) / f + oy) / sy,
                           (max(xs) / f + ox) / sx, (max(ys) / f + oy) / sy, float(sc)))
        return out


def read_line(reader, img: np.ndarray, region) -> str:
    """Eine einzelne Textzeile (Seitentitel, Name des Spielers) ohne die Suche nach Textkästen: 0,03 s statt 0,3 bis
    0,7 s (Messung 11.11.2026). Gibt '' zurück, wenn es nicht geht; der Aufrufer liest dann wie bisher."""
    try:
        h, w = img.shape[:2]
        sx, sy = w / REF_W, h / REF_H
        x0, y0, x1, y1 = region
        crop = img[int(max(0, y0 * sy)):int(min(h, y1 * sy)), int(max(0, x0 * sx)):int(min(w, x1 * sx))]
        if crop.size == 0:
            return ''
        res = reader.eng.text_recognizer([crop])
        rec = res[0] if isinstance(res, tuple) else res
        return str(rec[0][0]) if rec else ''
    except Exception:
        return ''


def find(boxes: list[Box], word: str, region=None, exact: bool = False, cutoff: float = 0.8) -> list[Box]:
    """Kästen, deren Text dem Wort entspricht (oder es enthält)."""
    w = norm(word)
    out = []
    for b in boxes:
        if region and not (region[0] <= b.cx <= region[2] and region[1] <= b.cy <= region[3]):
            continue
        n = b.n
        if not n:
            continue
        if n == w or (not exact and w in n):
            out.append(b)
        elif difflib.SequenceMatcher(None, n, w).ratio() >= cutoff:
            out.append(b)
    return out


# ---------------------------------------------------------------- Potential-Fenster

@dataclass
class Piece:
    slot: str | None = None
    set: str | None = None
    lv: int | None = None
    main: str | None = None           # Hauptwert, Schlüssel wie im Simulator (Power, Strength, AtkTech ...)
    rolls: dict = field(default_factory=dict)   # Nebenwerte {Schlüssel: Würfe}
    problems: list = field(default_factory=list)

    def label(self) -> str:
        subs = ', '.join(f'{k} {v}x' for k, v in sorted(self.rolls.items()))
        return f'{self.set} {self.slot} +{self.lv} | {self.main} | {subs}'


@dataclass
class Panel:
    x0: float
    x1: float
    piece: Piece
    button: str | None = None          # 'Equip', 'Unequip', None
    button_box: Box | None = None
    equipped: bool = False
    boxes: list = field(default_factory=list)


_ROMAN = re.compile(r'^(i{1,3}|iv|v|vi|l{1,3}|il|li|ll|lll)$')


def _rows(boxes: list[Box], tol: float = 13.0) -> list[list[Box]]:
    rows: list[list[Box]] = []
    for b in sorted(boxes, key=lambda b: b.cy):
        if rows and abs(rows[-1][0].cy - b.cy) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])
    return [sorted(r, key=lambda b: b.x0) for r in rows]


def merge_lines(boxes: list[Box], gap: float = 30.0) -> list[Box]:
    """Fasst Wörter einer Zeile zusammen, die dicht beieinander stehen ('Main' + 'Stats' -> 'Main Stats').
    Die Erkennung liefert sie meist schon zusammen, aber nicht immer."""
    out = []
    for row in _rows(boxes, tol=12.0):
        cur = None
        for b in row:
            if cur is not None and b.x0 - cur.x1 <= gap:
                cur = Box(cur.text + ' ' + b.text, cur.x0, min(cur.y0, b.y0), max(cur.x1, b.x1), max(cur.y1, b.y1),
                          min(cur.score, b.score))
            else:
                if cur is not None:
                    out.append(cur)
                cur = Box(b.text, b.x0, b.y0, b.x1, b.y1, b.score)
        if cur is not None:
            out.append(cur)
    return out


def _split_name_value(row: list[Box]) -> tuple[str, str] | None:
    """Ein Zeilen-Paar 'Power Attack' + '+35%'. OCR fasst manchmal beides zusammen."""
    txt = ' '.join(b.text for b in row)
    m = re.match(r'^\s*([A-Za-z][A-Za-z .]*?)\s*([+%0-9][0-9.,% +]*)\s*$', txt)
    if m:
        return m.group(1), m.group(2)
    # Wert vorne gelandet ('%96+ Strength')
    m = re.match(r'^\s*([%+0-9][0-9.,% +]*)\s*([A-Za-z][A-Za-z .]*)\s*$', txt)
    if m:
        return m.group(2), m.group(1)
    if len(row) >= 2:
        return ' '.join(b.text for b in row[:-1]), row[-1].text
    return None


def parse_panels(boxes: list[Box], slot_hint: str | None = None) -> list[Panel]:
    """Liest die Teile-Fenster (eins oder zwei nebeneinander, z. B. 'Equipped' links und das neue Teil rechts)."""
    lines = merge_lines(boxes)
    heads = sorted([b for b in lines if 'mainstats' in b.n], key=lambda b: b.x0)
    panels = []
    for i, h in enumerate(heads):
        x0 = h.x0 - 40
        # Das Fenster ist rechts von „Main Stats“ gut 350 breit. Mit 400 landete Text der Seite dahinter in der Zeile eines
        # Nebenwerts (Sugawara, Platz III: „Receive +4 %“ + „Set“ aus dem Stat Buff; Scan: „Set +4% +12“), und der Bot
        # hielt das richtige Teil für ein anderes (1.15).
        x1 = min(heads[i + 1].x0 - 40, h.x0 + 360) if i + 1 < len(heads) else h.x0 + 360
        mine = [b for b in boxes if x0 <= b.cx <= x1]
        sub_h = next((b for b in lines if x0 <= b.cx <= x1 and 'substats' in b.n and b.cy > h.cy), None)
        eff_h = next((b for b in lines if x0 <= b.cx <= x1 and 'seteffect' in b.n and b.cy > h.cy), None)
        piece = Piece(slot=slot_hint)
        pnl = Panel(x0, x1, piece, boxes=mine)
        # Titel und Stufe oberhalb von 'Main Stats'
        above = [b for b in mine if h.cy - 170 < b.cy < h.cy - 5]
        pnl.equipped = any('equipped' in b.n for b in above)
        for b in above:
            if 'equipped' in b.n:
                continue
            s = set_from_title(b.text)
            if s and not piece.set:
                piece.set = s
                tail = norm(b.text)[len(norm(s)):]
                if not piece.slot and tail:
                    t = tail.replace('l', 'i').replace('1', 'i')
                    if t in ('i', 'ii', 'iii', 'iv', 'v', 'vi'):
                        piece.slot = t.upper()
            m = re.match(r'^\+?(\d{1,2})$', b.text.strip().replace(' ', ''))
            if m and b.text.strip().startswith('+'):
                piece.lv = int(m.group(1))
        # Hauptwert
        y_sub = sub_h.cy if sub_h else h.cy + 70
        main_rows = _rows([b for b in mine if h.cy + 8 < b.cy < y_sub - 8])
        main_txt = None
        for row in main_rows:
            nv = _split_name_value(row)
            if nv:
                st = stat_from_text(nv[0])
                if st:
                    piece.main, main_txt = st, nv[1]
                    break
        # Nebenwerte
        y_eff = eff_h.cy if eff_h else y_sub + 160
        if sub_h:
            for row in _rows([b for b in mine if sub_h.cy + 8 < b.cy < y_eff - 8]):
                nv = _split_name_value(row)
                if not nv:
                    continue
                st = stat_from_text(nv[0])
                if not st:
                    piece.problems.append(T('Nebenwert nicht erkannt: {text}', text=repr(nv[0])))
                    continue
                val = first_value(nv[1])
                pct = '%' in val
                if st in BASIC and not pct:
                    # ein '%' kann verloren gehen: dann entscheidet das Raster (24er-Schritte flach, 2er-Schritte %)
                    if rolls_of(st + 'Flat', val) is None and rolls_of(st + 'Pct', val) is not None:
                        pct = True
                key = sub_key(st, pct)
                r = rolls_of(key, val)
                if r is None:
                    piece.problems.append(T('Wert nicht lesbar: {name} {value}', name=nv[0], value=repr(nv[1])))
                    continue
                piece.rolls[key] = piece.rolls.get(key, 0) + r
        else:
            piece.problems.append(T('Sub Stats nicht gefunden'))
        # Stufe aus dem Hauptwert, wenn '+12' nicht gelesen wurde (oder zur Kontrolle)
        if piece.slot and piece.main and main_txt:
            lv2 = level_from_main(piece.slot, piece.main, main_txt)
            if piece.lv is None:
                piece.lv = lv2
            elif lv2 is not None and lv2 != piece.lv:
                piece.problems.append(T('Stufe +{lv} passt nicht zum Hauptwert {value}', lv=piece.lv, value=repr(main_txt)))
        if piece.main is None:
            piece.problems.append(T('Hauptwert nicht erkannt'))
        if piece.set is None:
            piece.problems.append(T('Set nicht erkannt'))
        # Knopf unter dem Fenster
        below = [b for b in mine if b.cy > (eff_h.cy if eff_h else h.cy + 250)]
        for b in below:
            if b.n == 'unequip':
                pnl.button, pnl.button_box = 'Unequip', b
            elif b.n == 'equip' and pnl.button != 'Unequip':
                pnl.button, pnl.button_box = 'Equip', b
        panels.append(pnl)
    return panels


def same_piece(a: Piece, b: Piece, check_slot: bool = True) -> bool:
    if a.set != b.set or a.lv != b.lv or a.main != b.main:
        return False
    if check_slot and a.slot and b.slot and a.slot != b.slot:
        return False
    return dict(a.rolls) == dict(b.rolls)


def piece_diff(want: Piece, got: Piece) -> str:
    d = []
    for k in ('set', 'lv', 'main'):
        if getattr(want, k) != getattr(got, k):
            d.append(T('{key}: {got} statt {want}', key=k, got=getattr(got, k), want=getattr(want, k)))
    if dict(want.rolls) != dict(got.rolls):
        d.append(T('Nebenwerte {got} statt {want}', got=dict(got.rolls), want=dict(want.rolls)))
    return '; '.join(d) or T('gleich')


# ---------------------------------------------------------------- Bildhilfen

def mean_color(img: np.ndarray, region) -> tuple[float, float, float]:
    """Mittlere Farbe (B, G, R) eines Bereichs in Referenz-Koordinaten."""
    h, w = img.shape[:2]
    sx, sy = w / REF_W, h / REF_H
    x0, y0, x1, y1 = region
    a = img[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]
    if a.size == 0:
        return (0.0, 0.0, 0.0)
    m = a.reshape(-1, 3).mean(0)
    return float(m[0]), float(m[1]), float(m[2])


def golden_ratio(img: np.ndarray, region) -> float:
    """Anteil goldener/oranger Pixel (Rahmen und Hintergrund von Legendary-Teilen)."""
    h, w = img.shape[:2]
    sx, sy = w / REF_W, h / REF_H
    x0, y0, x1, y1 = region
    a = img[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]
    if a.size == 0:
        return 0.0
    hsv = cv2.cvtColor(a, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = (hh >= 12) & (hh <= 32) & (ss >= 110) & (vv >= 150)
    return float(mask.mean())


def changed(a: np.ndarray, b: np.ndarray, region=None) -> float:
    """Wie stark sich zwei Bilder unterscheiden (0 bis 1)."""
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    if region:
        h, w = a.shape[:2]
        sx, sy = w / REF_W, h / REF_H
        x0, y0, x1, y1 = region
        a = a[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]
        b = b[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]
    d = cv2.absdiff(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY))
    return float((d > 25).mean())
