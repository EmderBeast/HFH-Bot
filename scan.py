"""Account-Scan: liest den Account aus dem Spiel für den CC-Simulator.

Ergebnis: scans/hfh-scan-JJJJ-MM-TT_HHMM.json ({hfhscan: 1, players, pots, memories, collections, bonds, cheers,
coaches}).
Im Simulator: Speichern & Laden -> HFH-Bot -> „Scan-Datei laden“.
Der Scan ändert nichts im Spiel: Er öffnet nur Seiten, Reiter und Fenster und blättert in Listen. Er beginnt auf dem
Startbildschirm und geht am Ende dorthin zurück.
"""
from __future__ import annotations

import difflib
import re
import statistics
import time

import cv2
import numpy as np

import layout as L
import vision as V
from bot import Bot, BotError, same_key

# Höchstzahl Pfeil-Schritte in den Spieler-Schleifen (Manage Players zeigt auch Doppel, z. B. 1348 Karten)
STEP_LIMIT = 4000
# So viele doppelte Karten hintereinander: dann ist die Liste zu Ende
MAX_DUP_RUN = 150
# Manage Players: Knopf Filter (öffnet das Menü Deploy / Level / Star Level / Rarity / Position) und das Menü
MP_SORT = (1420, 806)
MP_SORT_MENU = (1100, 330, 1580, 790)
# Reiter Skills: Mitte der Zeile "Level x/y" des ersten Skills und Abstand der Skills (gemessen 07.11.2026, Lev Haiba)
SKILL_Y0, SKILL_DY = 222, 151
# Reiter Growth am Level-Cap: Zeile "Level Cap 70/70 >>> 70/80"
PS_LEVELCAP = (1000, 290, 1460, 345)
from device import SafetyStop
from plan import GAME, NAMES
from texts import T
from vision import REF_H, REF_W, SLOTS, norm, same_piece

MEMS = {int(k): v for k, v in GAME['memories'].items()}
COLLS = {int(k): v for k, v in GAME['collections'].items()}
BONDS = {int(k): v for k, v in GAME['bonds'].items()}
CHEERS = GAME['cheers']
CHARS = {int(k): v for k, v in GAME['chars'].items()}
COACHES = {int(k): v for k, v in GAME.get('coaches', {}).items()}
PARTS = ['players', 'pots', 'memories', 'bonds', 'cheers', 'coaches']
CHEER_STAT = {'power': 'Power', 'quick': 'Quick', 'set': 'Set', 'serve': 'Serve', 'receive': 'Receive', 'block': 'Block',
              'save': 'Save'}
STAT_RX = re.compile(r'\b(power|quick|set|serve|receive|block|save)(?:\s*attack)?\s*\+?\s*(\d{1,3})\b')
LV_RX = re.compile(r'l[vy]\s*\.?\s*(\d)\b', re.I)
TABS = {'growth': L.PS_GROWTH, 'skills': L.PS_SKILLS, 'reson': L.PS_RESON}
TAB_NAME = {'growth': 'Growth', 'skills': 'Skills', 'reson': 'Skill Resonance'}
TAB_NEXT = {'growth': ['skills', 'reson'], 'reson': ['skills', 'growth'], 'skills': ['reson', 'growth']}
MAX_RX = re.compile(r'\(?\s*max\s*:?\s*(\d+(?:\.\d+)?)\s*%?\s*\)?', re.I)
NUM_RX = re.compile(r'\d+(?:\.\d+)?')


# ================================================================ Text mit den Spieldaten abgleichen
def ratio(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def partial(frag, text):
    """Wie gut ein Stück Text irgendwo in einen längeren passt (0..1)."""
    if not frag or not text:
        return 0.0
    if frag in text:
        return 1.0
    n = len(frag)
    if n >= len(text):
        return ratio(frag, text)
    return max(ratio(frag, text[i:i + n]) for i in range(len(text) - n + 1))


def mem_desc(m, tier):
    """Die Wirkung einer Memory mit den Zahlen ihrer Stufe, ohne Leerzeichen."""
    ps = m.get('params') or []

    def sub(mm):
        i = int(mm.group(1))
        if i >= len(ps):
            return ''
        parts = str(ps[i]).split('/')
        return parts[min(len(parts), max(1, tier)) - 1]
    return norm(re.sub(r'\{(\d+)\}', sub, m.get('desc') or ''))


def match_memory_name(name):
    """ID einer Memory aus ihrem vollen Namen ('Quick Attack Boost: SHOYO HINATA (Jazz)'). None, wenn unsicher."""
    n = norm(name)
    if len(n) < 6:
        return None
    sc = sorted(((ratio(n, norm(m['name'])), mid) for mid, m in MEMS.items()), reverse=True)
    if sc[0][0] >= 0.82 and (sc[0][0] == 1.0 or len(sc) < 2 or sc[0][0] - sc[1][0] >= 0.02):
        return sc[0][1]
    return None


def match_memory_effect(frags, effect, tier=1, pos=None):
    """ID einer Memory aus ihrer Wirkung (mit den Zahlen der Stufe) und Stücken des Namens. None, wenn unsicher."""
    eff = norm(effect or '')
    if len(eff) < 15:
        return None
    fr = [norm(f) for f in frags or [] if len(norm(f)) >= 4]
    sc = []
    for mid, m in MEMS.items():
        s = ratio(eff, mem_desc(m, tier or 1))
        if fr:
            n = norm(m['name'])
            s += 0.4 * max(partial(f, n) * min(1.0, len(f) / 12) for f in fr)
        if pos and m.get('pos') == pos:
            s += 0.03
        sc.append((s, mid))
    sc.sort(reverse=True)
    if sc[0][0] < 0.55 or (len(sc) > 1 and sc[0][0] - sc[1][0] < 0.04):
        return None
    return sc[0][1]


_LV_RE = re.compile(r'(?i)(?<![a-z0-9])[l1i|][vy]\s*[.,:;]?\s*(\d{1,3})(?![0-9OoDQIl|])')


def lv_label(text):
    """Stufe aus dem Schild einer Kachel: „Lv.12“ -> 12 (auch „Ly.12“, „Lv 12“, „Lv.12/20“); None, wenn dort keins steht
    oder die Zahl nicht sicher ist („Lv.1O“)."""
    m = _LV_RE.search(text or '')
    return int(m.group(1)) if m else None


def tier_of(lines):
    """'Memory Tier IV' -> 4 (aus einer Liste von Textzeilen)."""
    for s in lines:
        t = s.lower().replace(' ', '')
        i = t.find('tier')
        if i < 0:
            continue
        tail = t[i + 4:i + 9]
        for a, b in (('ⅰ', 'i'), ('ⅱ', 'ii'), ('ⅲ', 'iii'), ('ⅳ', 'iv'), ('ⅴ', 'v'), ('|', 'i'), ('!', 'i'), ('1', 'i'),
                     ('l', 'i')):
            tail = tail.replace(a, b)
        m = re.match(r'(iv|v|i{1,3})(?![a-z])', tail)
        if m:
            return {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5}[m.group(1)]
    return None


def bond_title(text):
    """'The Guardian Deity Lv.5' -> ('The Guardian Deity', 5). Ohne 'Lv.': Stufe None."""
    text = re.sub(r'(?i)deployment\s*bond', ' ', text)
    ms = list(LV_RX.finditer(text))
    if not ms:
        return text.strip(), None
    m = ms[-1]
    return text[:m.start()].strip(), int(m.group(1))


def char_name_score(texts, cid):
    """Wie gut gelesene Texte zu einem Charakter passen (für gleichnamige Bonds). Zur Hälfte zählt, wie gut der ganze
    Name passt (1.16): "TORU OIKAWA" steckt auch in "TORU OIKAWA (School Festival)", ist aber ein anderer Spieler."""
    full = norm(NAMES.get(cid, ''))
    return max(((partial(norm(t), full) + ratio(norm(t), full)) / 2 for t in texts if len(norm(t)) >= 5), default=0.0)


# Kurzname einer Schule im Spiel -> Name in den Daten der Seite (gesehen: "SEIJOH's Senior Year Pillars" im Spiel,
# "AOBA JOHSAI's Senior Year" in den Daten)
BOND_ALIAS = {'seijoh': 'aobajohsai', 'seljoh': 'aobajohsai', 'se1joh': 'aobajohsai'}   # I wird oft als l oder 1 gelesen


def match_bond(name, kind=None, members=()):
    """(Art, ID) eines Bonds aus seinem Namen. kind: 'bonds' (Deployment Bond), 'collections' oder None (beides)."""
    n = norm(name)
    if len(n) < 3:
        return None
    for short, full in BOND_ALIAS.items():          # das Spiel schreibt die Schule manchmal kurz
        if short in n and full not in n:
            n = n.replace(short, full)
    pools = {'bonds': [('bonds', BONDS)], 'collections': [('collections', COLLS)]}.get(
        kind, [('collections', COLLS), ('bonds', BONDS)])
    c = match_bond_all(n, pools, members)
    return (c[0][0], c[0][1]) if c else None


def match_bond_all(n, pools, members=()):
    """Alle Bonds mit diesem Namen als [(Art, ID, Passung der Mitglieder)], der passendste zuerst (1.16). 30 Namen gibt es
    zwei- oder dreimal (derselbe Bond mit einer anderen Karte eines Spielers, z. B. "A Bit Higher!" mit Oikawa und mit
    Oikawa (School Festival))."""
    def score(name):
        m = norm(name)
        r = ratio(n, m)
        if r < 0.8 and len(m) >= 12 and partial(m, n) >= 0.92:
            r = 0.8                                  # das Spiel hängt ein Wort an ("... Senior Year Pillars")
        return r
    sc = sorted(((score(b['name']), k, bid) for k, pool in pools for bid, b in pool.items()), key=lambda x: -x[0])
    if not sc or sc[0][0] < 0.8:
        return []
    top = [x for x in sc if x[0] >= sc[0][0] - 0.01]

    def fit(x):
        if not members:
            return 0.0
        ids = (BONDS if x[1] == 'bonds' else COLLS)[x[2]]['chars']
        ids = [int(i) for i in (ids.keys() if isinstance(ids, dict) else ids)]
        return sum(char_name_score(members, i) for i in ids) / max(1, len(ids))
    out = [(x[1], x[2], fit(x)) for x in top]
    out.sort(key=lambda x: -x[2])
    return out


def bond_pools(kind):
    return {'bonds': [('bonds', BONDS)], 'collections': [('collections', COLLS)]}.get(
        kind, [('collections', COLLS), ('bonds', BONDS)])


def bond_norm(name):
    n = norm(name)
    for short, full in BOND_ALIAS.items():
        if short in n and full not in n:
            n = n.replace(short, full)
    return n


def match_cheer(name):
    n = norm(name)
    if len(n) < 4:
        return None
    sc = sorted(((ratio(n, norm(c['name'])), cid) for cid, c in CHEERS.items()), reverse=True)
    return sc[0][1] if sc[0][0] >= 0.75 else None


def cheer_stars(cid, a, d):
    """Sterne einer Cheer aus ihren Werten (Tabelle des Simulators)."""
    tab = GAME.get('cheerStars', {}).get(CHEERS[cid]['r'], {})
    vals = sorted(a.values(), reverse=True)
    best, bd = None, 1e9
    for st, v in tab.items():
        if not st.isdigit():
            continue
        dist = sum(abs(x - y) for x, y in zip(sorted(v[0], reverse=True), vals))
        if d:
            dist += abs(v[1][0] - d[0]) + 3 * abs(v[1][1] - d[1])
        if dist < bd:
            best, bd = int(st), dist
    return best


def label_name(text):
    """'Lv.80 YU NISHINOY' -> 'yunishinoy' (Name unter einer Kachel, ohne Stufe und Zahlen)."""
    return norm(re.sub(r'(?i)\bl[vy]\s*\.?\s*\d+|\d+', ' ', text))


def suffix_covered(suf, frags):
    """Wie viele Buchstaben des Zusatzes (z. B. "wintersports") in den gelesenen Stücken zu sehen waren, auch über
    mehrere Blicke verteilt ("(Winter" und "terSports)"). Gezählt werden nur Übereinstimmungen ab 3 Buchstaben."""
    seen = [False] * len(suf)
    for n in frags:
        for blk in difflib.SequenceMatcher(None, n, suf, autojunk=False).get_matching_blocks():
            if blk.size >= 3:
                for i in range(blk.b, blk.b + blk.size):
                    seen[i] = True
    return sum(seen)


def longest_common(a, b):
    """Länge des längsten gemeinsamen Stücks zweier Texte."""
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size


def sub_score(lab, full):
    """Wie gut ein Stück Laufschrift (abgeschnitten, vorne oder hinten) irgendwo in einen vollen Namen passt."""
    if not lab or not full:
        return 0.0
    if len(lab) >= len(full):
        return ratio(lab[:len(full)], full)
    return max(ratio(lab, full[i:i + len(lab)]) for i in range(len(full) - len(lab) + 1))


def prefix_score(lab, full):
    """Wie gut ein abgeschnittener Name zum Anfang eines vollen Namens passt."""
    if not lab or not full:
        return 0.0
    return ratio(lab, full[:len(lab)])


def align(labels, names):
    """Ordnet die Einträge einer Liste mit abgeschnittenen Namen den Spielern zu (gleiche Reihenfolge wie die Pfeile).
    Gibt für jedes Label den Index in names zurück (oder None)."""
    out, j = [], 0
    full = [norm(n or '') for n in names]
    for lab in labels:
        best = None
        for jj in range(max(0, j - 2), min(len(full), j + 4)):
            sc = prefix_score(lab, full[jj])
            if sc < 0.75:
                continue
            if best is None or sc > best[0] + 0.05 or (abs(sc - best[0]) <= 0.05 and abs(jj - j) < abs(best[1] - j)):
                best = (sc, jj)
        out.append(best[1] if best else None)
        if best:
            j = best[1] + 1
    return out


def piece_dict(pc):
    return {'slot': pc.slot, 'set': pc.set, 'lv': pc.lv, 'main': pc.main, 'rolls': dict(pc.rolls)}


# ================================================================ Coaches
def _pa_template(p):
    return {'id': p['id'], 't': p['t'], 'max': p['max'], 'order': [int(i) for i in re.findall(r'\{(\d)\}', p['txt'])],
            'pct': [bool(re.search(r'\{%d\}%%' % i, p['txt'])) for i in range(len(p['max']))],
            'skel': norm(re.sub(r'\{\d\}', ' ', p['txt']))}


PA_TEMPLATES = [_pa_template(p) for p in GAME.get('coachPA', [])]


def num(x):
    return int(x) if x == int(x) else x


def coach_by_name(text):
    """ID eines Coaches aus dem Namen über seinen Werten ('NOBUTERU IRIHATA')."""
    n = norm(text)
    if len(n) < 4 or not COACHES:
        return None
    sc = sorted(((ratio(n, norm(c['name'])), cid) for cid, c in COACHES.items()), reverse=True)
    return sc[0][1] if sc[0][0] >= 0.75 else None


def coach_rarity(cid, text):
    """Seltenheit eines Coaches (1 Rare, 2 Epic, 3 Legendary) aus den Zahlen der Expert Guidance."""
    nums = [float(x) for x in NUM_RX.findall(MAX_RX.sub(' ', text or ''))]
    best, bs = None, 0
    for r in (3, 2, 1):
        s = 0
        for p in COACHES[cid]['params']:
            parts = str(p).split('/')
            if len(parts) > 1 and float(parts[min(len(parts), r) - 1]) in nums:
                s += 1
        if s > bs:
            best, bs = r, s
    return best


def pa_parts(text):
    """Werte (ohne die MAX-Angaben), MAX-Angaben und der Text ohne Zahlen, z. B. aus
    'Increases Basic Stats by 10%（MAX:11%）; when your side is behind, additionally increases Basic Stats by 10%（MAX:11%）'."""
    t = (text or '').replace('（', '(').replace('）', ')').replace('：', ':')
    maxes = [float(m.group(1)) for m in MAX_RX.finditer(t)]
    rest = MAX_RX.sub(' ', t)
    vals = [float(x) for x in NUM_RX.findall(rest)]
    return vals, maxes, norm(NUM_RX.sub(' ', rest))


def match_pa(text, tier=None):
    """Positional Advantage aus ihrem Text: {'t': id, 'v': Werte, 'sure': alle Zahlen gelesen} oder None."""
    vals, maxes, skel = pa_parts(text)
    if len(skel) < 10 or not PA_TEMPLATES:
        return None
    best = None
    for tp in PA_TEMPLATES:
        s = ratio(skel, tp['skel'])
        want = [tp['max'][i] for i in tp['order'] if i < len(tp['max'])]
        if maxes and len(maxes) == len(want) and all(abs(a - b) < 0.01 for a, b in zip(maxes, want)):
            s += 0.15
        if tier and tp['t'] == tier:
            s += 0.05
        if best is None or s > best[0]:
            best = (s, tp)
    if best[0] < 0.75:
        return None
    tp = best[1]
    v = list(tp['max'])
    for k, i in enumerate(tp['order']):
        if k < len(vals) and i < len(v):
            v[i] = num(vals[k])
    return {'t': tp['id'], 'v': v, 'sure': len(vals) >= len(tp['order'])}


def hue_name(hues, table):
    """Häufigster Farbton (OpenCV 0..180) -> Name aus table [(von, bis, name)]."""
    if len(hues) < 12:
        return None
    h = float(np.median(hues))
    for lo, hi, name in table:
        if lo <= h <= hi:
            return name
    return None


POS_HUE = [(0, 8, 'MB'), (8, 32, 'Li'), (38, 82, 'S'), (85, 120, 'WS'), (125, 165, 'OP'), (168, 180, 'MB')]
TIER_HUE = [(0, 7, 'r'), (8, 32, 'o'), (85, 120, 'b'), (125, 165, 'p'), (170, 180, 'r')]
POS_TEXT = {'op': 'OP', 'ws': 'WS', 's': 'S', 'mb': 'MB', 'li': 'Li', 'll': 'Li', 'l': 'Li'}


def merge_pas(a, b, n):
    """Die Positional Advantages von oben (a) und vom Ende der Liste (b) zusammenlegen; n: wie viele es sein sollen.
    Gibt (Liste, sicher) zurück: sicher, wenn sich a und b passend überlappen und es n sind."""
    key = lambda x: (x['t'], x['pos'])   # noqa: E731
    if n is not None and len(a) >= n:
        return a[:n], True
    if n is not None:
        o = len(a) + len(b) - n
        if 0 <= o <= min(len(a), len(b)) and [key(x) for x in a[len(a) - o:]] == [key(y) for y in b[:o]]:
            return a + b[o:], True
    for k in range(min(len(a), len(b)), 0, -1):          # sonst die längste passende Überlappung
        if [key(x) for x in a[-k:]] == [key(y) for y in b[:k]]:
            m = a + b[k:]
            return m, n is None or len(m) == n
    return a + b, n is None


# ================================================================ Der Scan
class Scanner(Bot):
    """Liest Spieler, Potentials, Memories, Bonds und Cheers. Tippt nur auf Seiten, Reiter, Pfeile, Listen und Fenster."""

    PAGES = (('manageplayers', 'manage'), ('playerstats', 'stats'), ('playergrowth', 'stats'),
             ('featuredmemory', 'memory'), ('playerpotential', 'pp'), ('potentialinventory', 'inv'),
             ('manageinventory', 'manage_inv'), ('bondgallery', 'bond_gallery'), ('bondinfo', 'bond_info'),
             ('memorygallery', 'mem_gallery'), ('playerbond', 'player_bond'), ('teamlineup', 'team'),
             ('teamcheer', 'cheer'), ('gallery', 'gallery'), ('coach', 'coach'))

    def __init__(self, dev, reader, log=print, max_players=1000, debug_dir=None, save=None):
        super().__init__(dev, reader, log, dry=True, max_players=max_players, debug_dir=debug_dir)
        self.save = save or (lambda data: None)
        self.data = {'hfhscan': 1, 'date': time.strftime('%Y-%m-%d %H:%M'), 'tool': 'HFH-Bot'}
        self.notes = []                  # Hinweise für die Zusammenfassung am Ende
        self.gallery_players = None      # "Player 136/146" in der Gallery
        self.rarities = None             # z. B. {'SP', 'UR', 'SSR'}: nur diese Spieler lesen
        self.sorted_rarity = False
        self.sort_level = False          # Manage Players vorher nach Level sortieren (1.15), danach wieder Deploy
        self.sorted_level = False
        self.seen_players = []           # Spieler, die die Pfeile auf Player Stats gezeigt haben
        self.lv1_stop = False            # nur Spieler ab self.min_level lesen und bei der ersten Karte darunter aufhören (1.15)
        self.min_level = 2               # Mindeststufe dafür (potbot.ini: min_level, 2 bis 70)
        self.lv1_cards = []              # Lv.1-Karten, die vor dem Ende noch zu sehen waren: [{'id', 'name'}]
        self.mem_inline = True           # Memory gleich beim Spieler mitlesen (1.15); False nach einem Fehler dabei
        self.mem_done = set()            # Spieler-IDs, deren Memory so schon gelesen ist

    def warn(self, msg):
        self.notes.append(msg)
        self.log('  ' + msg)

    # ============================================================ Seiten
    def zones(self, kind):
        """Gesperrte Bereiche der offenen Seite für die Sicherheitsprüfung beim Tippen."""
        self.dev.zones = None if kind == 'pp' else L.ZONES.get(kind, [])

    def is_home(self, img):
        t = self.text_of(img, (1040, 520, 1600, 900))
        return 'gallery' in t and ('story' in t or 'pathtohonor' in t or 'inventory' in t)

    def page(self, img=None):
        img = self.snap() if img is None else img
        t = norm(V.read_line(self.reader, img, self.TITLE_LINE))      # schnell (1.15); unklar: wie bisher
        for key, kind in self.PAGES:
            if key in t and key not in ('gallery', 'coach'):
                return kind
        t = self.text_of(img, L.TITLE)
        for key, kind in self.PAGES:
            if key in t:
                return kind
        return 'home' if self.is_home(img) else 'other'

    def cheer_popup(self, img):
        t = self.text_of(img, L.TC_POP_TEXT)
        return 'activestats' in t or 'deploymentstats' in t

    def home(self):
        """Zurück zum Startbildschirm. Nie den Zurück-Knopf auf dem Startbildschirm: dort sitzt das Profilbild."""
        unknown = 0
        for _ in range(14):
            img = self.snap()
            kind = self.page(img)
            if kind == 'home':
                self.zones('home')
                return img
            if kind == 'other' and unknown == 0:
                unknown += 1                       # vielleicht baut sich die Seite noch auf
                time.sleep(1.2)
                continue
            unknown = 0
            self.zones(kind)
            if kind in ('inv', 'other') and self.filter_open(img):
                self.zones('inv')
                self.dev.tap(*L.F_CLOSE, 'Filter schließen')
                continue
            if kind == 'cheer' and self.cheer_popup(img):
                self.dev.tap(*L.TC_POP_CLOSE, 'Fenster schließen', wait=0.8)
                continue
            if kind == 'pp' and self.popup_open(img):
                self.dev.tap(*L.PP_CLOSE, 'Fenster schließen')
                continue
            if kind == 'inv' and self.popup_open(img, L.INV_PANELS):
                self.dev.tap(*L.INV_CLOSE, 'Fenster schließen')
                continue
            self.dev.tap(*L.BACK, 'Zurück', wait=1.5)
        self.shot('kein_startbildschirm')
        raise BotError(T('Der Startbildschirm ist nicht zu finden. Bitte im Spiel den Startbildschirm öffnen und neu starten.'))

    def enter(self, kind, xy, purpose, timeout=10):
        """Tippt und wartet, bis die Seite offen ist."""
        self.dev.tap(*xy, purpose, wait=1.2)
        img, ok = self.wait(lambda im: self.page(im) == kind, timeout=timeout, interval=0.5)
        if not ok:
            self.shot('seite_' + kind)
            raise BotError(T('Die Seite „{page}“ ging nicht auf.', page=purpose))
        self.zones(kind)
        return img

    def back_to(self, kind, timeout=8):
        """Ein Schritt zurück, bis die Seite kind offen ist."""
        self.dev.tap(*L.BACK, 'Zurück', wait=1.2)
        img, ok = self.wait(lambda im: self.page(im) == kind, timeout=timeout, interval=0.5)
        if not ok:
            raise BotError(T('Zurück zur Seite „{page}“ ging nicht.', page=kind))
        self.zones(kind)
        return img

    def sort_by(self, what):
        """Manage Players: Sortierung wählen ('rarity', 'deploy', ...). Das Menü geht über dem Knopf Filter auf."""
        self.dev.tap(*MP_SORT, 'Sortierung öffnen', wait=1.0)
        for _ in range(3):
            boxes = self.ocr(self.snap(), MP_SORT_MENU)
            # 13.11.2026: bei einem Spieler fand er „level“ nicht (anders gelesen); daher auch „lv“ und Teile des Worts
            alias = {'level': ('level', 'lv', 'lvl'), 'deploy': ('deploy',), 'rarity': ('rarity',)}.get(what, (what,))
            hit = ([b for b in boxes if b.n == what] or [b for b in boxes if b.n in alias]
                   or [b for b in boxes if any(a in b.n for a in alias) and len(b.n) <= len(what) + 4])
            if not hit and what == 'level':
                # Das Menü ist halb durchsichtig: „Level“ liegt über dem Namen einer Karte und wird mit ihm zusammen
                # gelesen (z. B. „wintespolevel“; Screenshot 13.11.2026). Dann zählt auch ein Text, der auf level endet
                hit = [b for b in boxes if b.n.endswith('level') and 'star' not in b.n]
            if not hit:
                # Die Einträge stehen immer in derselben Reihenfolge untereinander: aus den gelesenen Nachbarn die Stelle
                # des gesuchten Eintrags ausrechnen
                order = ['deploy', 'level', 'starlevel', 'rarity', 'position']
                if what in order:
                    got = {}
                    for bx in boxes:
                        if bx.n in order and bx.n not in got:
                            got[bx.n] = bx
                    if len(got) >= 2:
                        ks = sorted(got, key=order.index)
                        k0, k1 = ks[0], ks[-1]
                        step = (got[k1].cy - got[k0].cy) / (order.index(k1) - order.index(k0))
                        if step > 20:
                            y = got[k0].cy + step * (order.index(what) - order.index(k0))
                            self.dev.tap(got[k0].cx, y, 'Sortierung ' + what + ' (aus der Reihenfolge)', wait=1.5)
                            return True
            if hit:
                self.dev.tap(hit[0].cx, hit[0].cy, 'Sortierung ' + what, wait=1.5)
                return True
            time.sleep(0.6)
        self.shot('sortierung')
        self.dev.tap(*MP_SORT, 'Sortierung schließen', wait=0.8)
        self.warn(T('Die Sortierung „{s}“ war nicht zu finden', s=what))
        return False

    def restore_sort(self):
        """Spielerliste wieder nach Deploy sortieren, wenn der Bot sie umgestellt hat."""
        if not (self.sorted_level or self.sorted_rarity):
            return
        try:
            self.home()
            self.enter('manage', L.HOME_PLAYER, 'Player')
            if self.sort_by('deploy'):
                self.sorted_level = self.sorted_rarity = False
                self.log(T('Spielerliste wieder nach Deploy sortiert.'))
            self.home()
        except BotError as e:
            self.log(T('Sortierung zurückstellen ging nicht ({err}); bitte in „Manage Players“ wieder „Deploy“ wählen.', err=e))

    def rarity_ok(self, pid):
        """Bei „nur SP, UR und SSR“: gehört der Spieler dazu? (Unbekannte Spieler: ja, sie fallen später heraus.)"""
        if not self.rarities or pid is None:
            return True
        return (GAME['chars'].get(str(pid)) or {}).get('rarity') in self.rarities

    def first_player(self):
        """Manage Players: Liste ganz nach oben, ersten Spieler antippen. Dort fangen die Pfeile und die Memory-Liste an."""
        if self.rarities and not self.sorted_rarity:
            self.sorted_rarity = self.sort_by('rarity')
        # Nach Level sortiert (hoch nach niedrig) stehen alle hochgelevelten Spieler vorn, egal welche Seltenheit; mit
        # „Deploy“ stand z. B. Watari (SR) erst hinter gut 65 Karten, die Pfeile erreichten ihn nicht (1.15). Ein zweites
        # Tippen auf „Level“ dreht die Richtung nicht um (getestet am 11.11.2026).
        if self.sort_level and not self.sorted_level:
            self.sorted_level = self.sort_by('level')
            if self.sorted_level:
                self.log(T('  Spielerliste nach Level sortiert (am Ende wieder Deploy).'))
        x = (L.MP_GRID[0] + L.MP_GRID[2]) // 2
        self.top(x, L.MP_GRID[1] + 170, x, L.MP_GRID[3] - 20, region=L.MP_GRID)
        self.dev.tap(*L.MP_FIRST, 'ersten Spieler auswählen', wait=1.0)

    def players_page(self, kind, button, purpose):
        """Vom Startbildschirm über Manage Players (erster Spieler) auf eine Spielerseite."""
        self.home()
        self.enter('manage', L.HOME_PLAYER, 'Player')
        self.first_player()
        return self.enter(kind, button, purpose)

    # ============================================================ Spieler direkt in Manage Players öffnen (1.14)
    def tile_sig(self, img, cx, strip_y):
        """Kleines Graubild des Gesichts einer Kachel (über ihrem Namensstreifen), um gleiche Karten zu erkennen."""
        g = L.MP_GRID
        reg = (cx - 55, max(strip_y - 175, g[1]), cx + 45, min(strip_y - 75, g[3]))
        if reg[3] - reg[1] < 60:
            return None                               # Kachel oben abgeschnitten
        x0, y0, x1, y1 = self.px(reg, img)
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (10, 10), interpolation=cv2.INTER_AREA).astype(np.float32)

    def open_pp_for(self, pid, start=0, most=45):
        """Sucht die Kachel eines Spielers in „Manage Players“, wählt sie aus und öffnet dort Player Potential.
        Gibt den Bildschirm (Zahl der Züge vom Anfang der Liste) zurück, auf dem sie stand; None: nicht gefunden.

        Die Liste hat bei Emre 1385 Karten, die meisten doppelt; mit den Pfeilen dauert der Weg über eine Stunde
        (Lauf vom 11.11.2026). Hier liest der Bot auf jedem Bildschirm nur die Namensstreifen unten auf den Kacheln
        (dort läuft der Name als Laufschrift, zu sehen ist immer nur ein Stück) und tippt allein die Kacheln an, deren
        Stück im gesuchten Namen vorkommt. Links steht dann der ganze Name: der entscheidet. Karten, die genauso aussehen
        wie eine schon geprüfte, tippt er nicht noch einmal an. Jeden Bildschirm sieht er zweimal an, weil die Laufschrift
        gerade nur einen Buchstaben zeigen kann."""
        name = NAMES.get(pid)
        if not name:
            return None
        want = norm(name)
        self.home()
        self.enter('manage', L.HOME_PLAYER, 'Player')
        x = L.MP_COLS[1]
        self.top(x, L.MP_GRID[1] + 170, x, L.MP_GRID[3] - 20, region=L.MP_GRID)

        def drag():
            before = self.snap()
            # nur gut eine Reihe weiter (1.15): bei 440 rutschten zwei Reihen durch, und ein Namensstreifen konnte auf
            # beiden Bildschirmen am Rand abgeschnitten sein (Watari im Lauf vom 11.11.2026 einmal übersehen)
            if not self.dev.sdrag(x, 600, x, 330, ms=500, hold=0.35, wait=0.4):
                self.dev.swipe(x, 600, x, 330, ms=1500, wait=0.6)
            return self.moved(before, self.settle(L.MP_GRID, timeout=1.5), L.MP_GRID) >= 15

        for _ in range(start):
            if not drag():
                break
        others = []                                   # Gesichter von Kacheln, die jemand anderes waren
        taps = 0
        for n in range(start, start + most):
            for look in (0, 1):
                if look:
                    time.sleep(0.7)
                img = self.snap()
                boxes = [b for b in self.ocr(img, L.MP_GRID)
                         if len(b.n) >= 2 and b.n.isalpha() and b.n not in ('ws', 'mb', 'op', 'li') and b.n in want]
                for b in sorted(boxes, key=lambda b: (round(b.cy / 40), b.cx)):
                    cx = min(L.MP_COLS, key=lambda c: abs(c - b.cx))
                    if abs(cx - b.cx) > 95:
                        continue
                    sig = self.tile_sig(img, cx, b.cy)
                    if sig is not None and any(float(np.abs(sig - o).mean()) < 12 for o in others):
                        continue                      # dieselbe Karte wie eine schon geprüfte (Doppel)
                    before = self.snap()
                    self.dev.tap(cx, max(b.cy - 45, L.MP_GRID[1] + 8), 'Spieler in der Liste ansehen', wait=0.4)
                    taps += 1
                    im = self.wait_change(before, L.PP_NAME, timeout=1.2)
                    here = self.player_here(im)[0]
                    if here != pid:
                        time.sleep(0.4)
                        here = self.player_here()[0]
                    if here == pid:
                        self.enter('pp', L.MP_POTENTIAL, 'Potential')
                        time.sleep(0.6)
                        if self.player_here()[0] != pid:
                            self.shot('direkt_falsch')
                            raise BotError(T('Player Potential zeigt nicht {name}', name=name))
                        self.log(T('    Kachel auf Bildschirm {n} gefunden ({k} Kacheln angetippt)', n=n + 1, k=taps))
                        return n
                    if sig is not None:
                        others.append(sig)
            if not drag():
                break
        self.shot('direkt_nicht_gefunden')
        self.log(T('    In „Manage Players“ keine Kachel für {name} gefunden ({k} Kacheln angetippt)', name=name, k=taps))
        return None

    # ============================================================ Listen durchgehen
    def px(self, region, img):
        h, w = img.shape[:2]
        x0, y0, x1, y1 = region
        return int(x0 * w / REF_W), int(y0 * h / REF_H), int(x1 * w / REF_W), int(y1 * h / REF_H)

    def settle(self, region, timeout=2.5):
        """Bildschirmfoto, sobald sich der Inhalt eines Bereichs nicht mehr bewegt."""
        t0 = time.time()
        a = self.snap()
        while time.time() - t0 < timeout:
            time.sleep(0.25)
            b = self.snap()
            if V.changed(a, b, region) < 0.004:
                return b
            a = b
        return a

    def shift(self, a, b, region, axis=1, expect=None, at_least=None):
        """Wie weit der Inhalt eines Bereichs von Bild a zu Bild b gewandert ist (Ref-Einheiten; + = nach oben bzw. links).
        Mehrere fast gleich gute Lösungen (Listen mit gleich aussehenden Reihen): nur die, die mindestens at_least weit
        sind (weniger als die Wischstrecke wandert eine Liste nur an ihrem Ende), davon die, die am besten zu expect passt.
        Gibt (Verschiebung, eindeutig) zurück, oder (None, False), wenn es sich nicht sagen lässt."""
        x0, y0, x1, y1 = self.px(region, a)
        A = cv2.cvtColor(a[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        B = cv2.cvtColor(b[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        if axis == 0:
            A, B = np.ascontiguousarray(A.T), np.ascontiguousarray(B.T)
        f = 2 if A.shape[0] > 300 else 1
        if f > 1:
            A = cv2.resize(A, (A.shape[1] // f, A.shape[0] // f), interpolation=cv2.INTER_AREA)
            B = cv2.resize(B, (B.shape[1] // f, B.shape[0] // f), interpolation=cv2.INTER_AREA)
        A, B = A.astype(np.float32), B.astype(np.float32)
        unit = f * (REF_H / a.shape[0] if axis == 1 else REF_W / a.shape[1])
        h = A.shape[0]
        if float(np.abs(A - B).mean()) < 1.0:
            return 0.0, True
        dmax = int(h * 0.8)
        ds = list(range(-dmax, dmax + 1))
        sc = np.full(len(ds), -1.0, np.float32)
        for i, d in enumerate(ds):
            p, q = (A[d:], B[:h - d]) if d >= 0 else (A[:h + d], B[-d:])
            p, q = p - p.mean(), q - q.mean()
            den = float(np.sqrt(float((p * p).sum()) * float((q * q).sum())))
            if den > 1e-3:
                sc[i] = float((p * q).sum()) / den
        best = float(sc.max())
        if best < 0.5:
            return None, False
        if sc[dmax] >= best - 0.005:
            return 0.0, True                      # nichts bewegt
        peaks = [i for i in range(len(ds)) if sc[i] >= best - 0.04 and (i == 0 or sc[i] >= sc[i - 1])
                 and (i == len(ds) - 1 or sc[i] >= sc[i + 1])]
        if len(peaks) <= 1:
            return ds[int(np.argmax(sc))] * unit, True
        cand = peaks
        if at_least is not None:
            lim = at_least / unit
            cand = [i for i in peaks if (ds[i] >= lim if lim >= 0 else ds[i] <= lim)] or peaks
        if expect is not None:
            e = expect / unit
            return ds[min(cand, key=lambda i: abs(ds[i] - e))] * unit, False
        return ds[max(cand, key=lambda i: sc[i])] * unit, False

    def walk(self, region, first, pitch, axis, read, swipe, limit=3000, drag=False):
        drag_mode = drag
        """Geht eine Liste Reihe für Reihe durch (axis 1: senkrecht, 0: waagerecht).
        first: Mitte der ersten Reihe, solange die Liste ganz oben/links steht; pitch: Abstand der Reihen.
        read(k, c) liest Reihe k, deren Mitte gerade bei c liegt; False heißt: hier ist die Liste zu Ende.
        swipe = (x0, y0, x1, y1, ms): langsame Wischbewegung, die die Liste weiterschiebt (Richtung, Länge höchstens).
        Wie weit die Liste wirklich gewandert ist, misst das Programm jedes Mal am Bild. Ist eine Reihe ungelesen
        oben hinausgerutscht, schiebt es die Liste ein Stück zurück. Gibt die Zahl der gelesenen Reihen zurück;
        lässt sich die Liste nicht sicher blättern, BotError (dann ist das Ergebnis unvollständig)."""
        lo, hi = (region[1], region[3]) if axis == 1 else (region[0], region[2])
        top_ok, bot_ok = lo + pitch * 0.3, hi - pitch * 0.45
        window = bot_ok - top_ok
        x0, y0, x1, y1, ms = swipe
        length = max(1.0, ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        rates, drates = [], []
        offset, k, back, end_tries = 0.0, 0, 0, 0
        prev = self.settle(region)
        while k < limit:
            while k < limit:
                c = first + k * pitch - offset
                if c > bot_ok or c < top_ok:
                    break
                if read(k, c) is False:
                    return k
                k += 1
            c = first + k * pitch - offset
            rate = statistics.median(rates) if rates else 1.12
            if c < top_ok:
                # zu weit geschoben: Reihe k ist ungelesen oben hinaus. Zurück, bis sie in der Mitte steht.
                back += 1
                if back > 6:
                    self.shot('liste')
                    raise BotError(T('Eine Liste ließ sich nicht genau genug blättern ({n} Reihen gelesen).', n=k))
                drag, sgn = max(60.0, min(length, (top_ok + window / 2 - c) / rate)), -1
            else:
                drag, sgn = max(60.0, min(length, 0.8 * window / max(rates + [1.4]))), 1
            ax, ay = mx - sgn * ux * drag / 2, my - sgn * uy * drag / 2
            bx, by = mx + sgn * ux * drag / 2, my + sgn * uy * drag / 2
            if drag_mode and axis == 1:
                ty = self.on_tile(prev, ax, ay)               # auf einer Kachel anfangen, nicht in der Lücke
                if ty is not None:
                    ay, by = ty, by + (ty - ay)
            # drag: über sendevent ziehen und am Ende still halten (die Liste läuft nicht nach); im Potential-Inventar
            # kommen langsame adb-Wischer oft nicht an (Tests 07.11.2026)
            if not (drag_mode and self.dev.sdrag(ax, ay, bx, by, ms=600, hold=0.4, wait=0.3)):
                self.dev.swipe(ax, ay, bx, by, ms=ms, wait=0.3)
            cur = self.settle(region)
            d, sure = self.shift(prev, cur, region, axis, expect=sgn * drag * rate,
                                 at_least=sgn * 0.85 * drag * min(rates + [1.0]))
            note = ''
            if drag_mode:
                # Gezogen (sendevent, ohne Nachlaufen) wandert die Liste sehr gleichmäßig (Faktor 0,92; Test 07.11.2026).
                # Sehen mehrere Reihen gleich aus, misst shift() leicht eine Reihe zu viel oder zu wenig; dann zählt
                # die erwartete Strecke, wenn sich das Bild überhaupt bewegt hat.
                exp = sgn * drag * (statistics.median(drates) if drates else 0.92)
                rx0, ry0, rx1, ry1 = self.px(region, cur)
                moved = float(np.abs(cur[ry0:ry1, rx0:rx1].astype(np.float32)
                                     - prev[ry0:ry1, rx0:rx1].astype(np.float32)).mean())
                if d is not None and sure and abs(d - exp) < 0.15 * pitch:
                    if abs(d) / drag > 0.8:
                        drates.append(abs(d) / drag)
                elif moved > 20:
                    note = f' -> geschätzt {exp:.0f} (Bild {moved:.0f})'
                    d, sure = exp, False
                else:
                    note = f' -> keine Bewegung (Bild {moved:.0f})'
                    d, sure = 0.0, True
            if getattr(self, 'debug_walk', False) or drag_mode:
                if getattr(self, 'debug_walk', False):
                    self.shot(f'walk_k{k}')
                self.log(f'    [walk] k={k} offset={offset:.0f} drag={sgn * drag:.0f} rate={rate:.2f} '
                         f'gemessen={d} sicher={sure} pitch={pitch}{note}')
            if d is None:
                self.shot('liste')
                raise BotError(T('Eine Liste ließ sich nicht sicher weiterblättern ({n} Reihen gelesen).', n=k))
            if sgn > 0 and d < pitch * 0.12 and end_tries < 6:
                # Manchmal bleibt die Liste hängen (im Potential-Inventar nach etwa 8 Reihen; Test 07.11.2026, danach ging
                # es nach einer Pause weiter): warten und an einer anderen Stelle noch einmal wischen
                end_tries += 1
                time.sleep(1.5)
                if not drag_mode:
                    mx += (-1) ** end_tries * 60 * abs(uy)    # abwechselnd links und rechts davon
                continue
            if sgn > 0 and d < pitch * 0.12:
                # Ende der Liste: die Reihen lesen, die noch zu sehen sind
                while k < limit:
                    c = first + k * pitch - offset
                    if c > hi - pitch * 0.2:
                        break
                    if c >= top_ok and read(k, c) is False:
                        return k
                    k += 1
                return k
            end_tries = 0
            if sure and abs(d) >= 0.75 * drag and not (drag_mode and note):   # nicht am Ende der Liste gemessen
                rates.append(abs(d) / drag)
            offset += d
            prev = cur
        return k

    def top(self, x0, y0, x1, y1, n=3, region=None, ms=None):
        """Liste mit schnellen Wischern an den Anfang schieben. Mit region: so lange, bis sich die Liste nicht mehr
        bewegt (Listen mit vielen Karten, die vorher weit unten standen: 3 Wischer reichen dann nicht)."""
        if region is None:
            for _ in range(n):
                self.dev.swipe(x0, y0, x1, y1, ms=ms or 200, wait=0.35)
            time.sleep(0.6)
            return
        # Nur langsame Wischer (2 s) bewegen die Listen im Spiel sicher (Tests 07.11.2026)
        prev, still = self.snap(), 0
        for _ in range(250):
            self.dev.swipe(x0, y0, x1, y1, ms=ms or 2000, wait=0.4)
            cur = self.snap()
            # nicht V.changed: die Namen laufen als Laufschrift, das Bild ändert sich also immer ein wenig
            d, _ = self.shift(prev, cur, region, 1)
            if d is not None and abs(d) < 8:
                still += 1
                if still >= 2:
                    break
            else:
                still = 0
            prev = cur
        time.sleep(0.6)

    def on_tile(self, img, x, y, span=100):
        """Nächste y-Stelle bei y, die in Spalte x sicher auf einer Kachel liegt (mindestens 40 von der Lücke zwischen
        zwei Reihen entfernt), sonst None. Im Potential-Inventar bewegt ein Wischer die Liste nur, wenn er auf einer
        Kachel anfängt, nicht in der Lücke (Emre, 07.11.2026). Die Lücken sind fast ohne Gold (unter 0,03)."""
        ys = list(range(int(y - span - 60), int(y + span + 60), 4))
        gap = [V.golden_ratio(img, (x - 60, yy - 3, x + 60, yy + 3)) < 0.03 for yy in ys]
        if not any(gap) or all(gap):
            return None
        gaps = [yy for yy, g in zip(ys, gap) if g]
        best = None
        for yy in ys:
            if abs(yy - y) > span:
                continue
            if min(abs(yy - g) for g in gaps) >= 40 and (best is None or abs(yy - y) < abs(best - y)):
                best = yy
        return best

    def fm_selected_y(self, img):
        """Featured Memory: Mitte der ausgewählten Kachel links (nur sie reicht mit goldenem Rahmen bis x 205-222)."""
        runs, cur = [], None
        for y in range(110, 892, 4):
            if V.golden_ratio(img, (205, y - 3, 222, y + 3)) > 0.2:
                cur = [y, y] if cur is None else [cur[0], y]
            elif cur is not None:
                runs.append(cur)
                cur = None
        if cur is not None:
            runs.append(cur)
        runs = [r for r in runs if r[1] - r[0] >= 40]
        if not runs:
            return None
        r = max(runs, key=lambda r: r[1] - r[0])
        return (r[0] + r[1]) / 2

    def moved(self, a, b, region):
        """Mittlerer Bildunterschied in region (ohne Bewegung etwa 3 bis 11, mit Bewegung um 50)."""
        x0, y0, x1, y1 = self.px(region, a)
        return float(np.abs(a[y0:y1, x0:x1].astype(np.float32) - b[y0:y1, x0:x1].astype(np.float32)).mean())

    def low_visible(self, img):
        """Steht im Potential-Inventar auf einer Kachel sicher eine Stufe unter +12 (+0 oder +3 bis +9)?
        (+10 und +11 nicht: am Rand abgeschnitten sieht +12 aus wie +1.)"""
        for b in self.ocr(img, L.INV_LIST):
            t = b.text.replace(' ', '').replace('O', '0').replace('o', '0')
            m = re.fullmatch(r'\+(\d{1,2})', t)
            if m and int(m.group(1)) in (0, 3, 4, 5, 6, 7, 8, 9):
                return True
        return False

    def to_end(self, region, x, ya, yb, ms=300, most=40, stop=None):
        """Mit schnellen Wischern bis ans Ende einer Liste, bis sich das Bild zweimal nicht mehr bewegt.
        Gemessen am mittleren Bildunterschied (nicht an shift(): gleich aussehende Reihen täuschen die Messung;
        das Glitzern der Kacheln macht ohne Bewegung etwa 10, eine Bewegung etwa 50; Test 07.11.2026).
        Gibt die Zahl der Wischer zurück, die die Liste noch bewegt haben."""
        prev, still, moved = self.settle(region), 0, 0
        x0, y0, x1, y1 = self.px(region, prev)
        for _ in range(most):
            if stop and stop(prev):
                break
            ty = self.on_tile(prev, x, ya)
            dy = 0 if ty is None else ty - ya
            self.dev.swipe(x, ya + dy, x, yb + dy, ms=ms, wait=0.3)
            time.sleep(0.8)                               # Schwung auslaufen lassen
            cur = self.settle(region)
            diff = float(np.abs(cur[y0:y1, x0:x1].astype(np.float32) - prev[y0:y1, x0:x1].astype(np.float32)).mean())
            if diff < 25:
                still += 1
                if still >= 2:
                    break
            else:
                still, moved = 0, moved + 1
            prev = cur
        return moved

    def saturation(self, img, region):
        """Mittlere Farbsättigung eines Bereichs (0..255): graue Karten haben wenig."""
        x0, y0, x1, y1 = self.px(region, img)
        a = img[max(0, y0):y1, max(0, x0):x1]
        return float(cv2.cvtColor(a, cv2.COLOR_BGR2HSV)[..., 1].mean()) if a.size else 0.0

    def wait_change(self, before, region, timeout=1.2):
        """Wartet, bis sich ein Bereich gegenüber dem Bild before verändert hat (oder die Zeit um ist)."""
        img, ok = self.wait(lambda im: V.changed(before, im, region) > 0.02, timeout=timeout, interval=0.2)
        return img

    # ============================================================ Spieler: Stufe, Skills, Rating
    def read_skills(self, img):
        """{'S1': 1, ...} aus den vier Zeilen 'Level 1/3' im Reiter Skills; None, wenn es nicht sicher geht.
        Ein Skill, der noch Training braucht ("Training Required", meist S3), hat keine Zeile "Level": er zählt als 0
        (so rechnet auch das Spiel die Skill-Summe, und der Simulator nimmt 0 an)."""
        rows, txt = [], []
        for b in V.merge_lines(self.ocr(img, L.PS_SKILLROWS)):
            t = b.text.lower().replace(' ', '')
            txt.append(t)
            m = re.search(r'level(\d{1,2})/(\d{1,2})', t)
            if m:
                rows.append((b.cy, int(m.group(1)), int(m.group(2))))
        rows.sort()
        if len(rows) == 4:
            return {f'S{i + 1}': lv for i, (_, lv, mx) in enumerate(rows)}
        if len(rows) == 3 and any('required' in t or 'training' in t for t in txt):
            out = {}
            for cy, lv, _ in rows:
                i = int(round((cy - SKILL_Y0) / SKILL_DY))
                if not 0 <= i <= 3 or f'S{i + 1}' in out:
                    return None
                out[f'S{i + 1}'] = lv
            for i in range(4):
                out.setdefault(f'S{i + 1}', 0)
            return dict(sorted(out.items()))
        return None

    def read_level(self, img):
        for up in (1.0, 2.0):
            t = ' '.join(b.text for b in V.merge_lines(self.ocr(img, L.PS_LEVEL, upscale=up))).lower()
            m = re.search(r'\bl[vy]\s*\.?\s*(\d{1,2})\b', t)
            if m and 1 <= int(m.group(1)) <= 80:
                return int(m.group(1))
        # Spieler am Level-Cap ("Lv. 70 (MAX)", darunter "Level Cap 70/70 >>> 70/80"): die erste Zahl ist die Stufe
        t = ''.join(b.text for b in sorted(self.ocr(img, PS_LEVELCAP), key=lambda b: b.x0)).lower().replace(' ', '')
        m = re.search(r'cap(\d{1,2})/(\d{1,2})', t)
        return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 80 else None

    def read_total(self, img):
        t = ''.join(b.n for b in self.ocr(img, L.PS_TOTAL))
        m = re.search(r'totalskilllevel(\d{1,2})', t)
        return int(m.group(1)) if m else None

    def read_number(self, img, region):
        t = ' '.join(b.text for b in self.ocr(img, region)).replace(',', '').replace('.', '')
        m = re.search(r'(\d{3,6})', t)
        return int(m.group(1)) if m else None

    def tab_value(self, tab, img):
        return {'growth': self.read_level, 'skills': self.read_skills, 'reson': self.read_total}[tab](img)

    def count_gallery(self):
        """Wie viele verschiedene Spieler du hast ("Player 136/146" in der Gallery). Damit wissen die Spieler-Schleifen,
        wann sie fertig sind: Manage Players zeigt jede Karte einzeln, auch Doppel (z. B. 1348 Karten bei 136 Spielern)."""
        try:
            self.home()
            img = self.enter('gallery', L.HOME_GALLERY, 'Gallery')
            t = ' '.join(b.text for b in self.ocr(img, L.GAL_PLAYER))
            m = re.search(r'(\d{1,3})\s*/\s*(\d{1,3})', t)
            if m:
                self.gallery_players = int(m.group(1))
                self.log(T('  Die Gallery zählt {n} Spieler.', n=self.gallery_players))
        except BotError:
            pass

    def skip_card(self):
        """Pfeil weiter über eine doppelte Karte. Gleiche Karten sehen gleich aus, darum wartet er nur kurz auf einen
        anderen Namen und geht sonst davon aus, dass die nächste Karte (wieder ein Doppel) offen ist."""
        pid0, _, raw0 = self.player_here(self.snap())
        self.dev.tap(*L.PP_NEXT, 'nächster Spieler', wait=0.6)

        def changed(im):
            pid, _, raw = self.player_here(im)
            if pid is not None:
                return pid != pid0
            return bool(raw) and difflib.SequenceMatcher(None, raw, raw0 or '').ratio() < 0.8
        _, r = self.wait(changed, timeout=2.0, interval=0.3)
        time.sleep(0.4 if r else 0.2)

    def all_seen(self, n):
        """Alle verschiedenen Spieler gelesen (laut Gallery)? Bei „nur SP, UR und SSR“ zählt die Gallery nicht."""
        return not self.rarities and self.gallery_players is not None and n >= self.gallery_players

    def scan_players(self):
        self.log(T('Spieler: Stufe, Skills und Rating ...'))
        if self.gallery_players is None:
            self.count_gallery()
        self.players_page('stats', L.MP_GROWTH, 'Growth')
        players, seen, cur = [], self.seen_players, 'growth'
        try:
            self.players_loop(players, seen, cur)
        except BotError as e:
            self.shot('fehler')
            self.warn(T('{part}: abgebrochen ({err}); was bis dahin gelesen ist, bleibt', part=T('Spieler'), err=e))
        self.log(T('  {n} Spieler gelesen.', n=len(players)))
        if self.lv1_stop:
            ids = {p.get('id') for p in players}
            self.lv1_cards = [c for c in self.lv1_cards if c['id'] not in ids]
            if self.lv1_cards:
                self.log(T('  Unter der Mindeststufe (nicht gelesen; zum Einsetzen erst hochleveln): {names}',
                           names=', '.join(f"{c['name']} (Lv.{c.get('lv', '?')})" for c in self.lv1_cards)))
        return players

    def players_loop(self, players, seen, cur):
        dups = run = 0
        lv1_run, last_lv1 = 0, None                    # „nur Spieler über Lv.1“: Lv.1-Karten in Folge, die letzte davon
        for _ in range(STEP_LIMIT):
            if len(players) >= self.max_players or self.all_seen(len(seen)):
                break
            img = self.snap()
            pid, name, raw = self.player_here(img)
            key = self.key_of(pid, raw)
            if seen and same_key(key, seen[0]):
                break                                   # einmal herum
            if not self.rarity_ok(pid):
                break                                   # nach Seltenheit sortiert: ab hier nur noch SR, R und N
            if any(same_key(key, s) for s in seen):
                if self.lv1_stop and last_lv1 is not None and same_key(key, last_lv1):
                    break                               # eine Lv.1-Karte und gleich ihr Doppel: ab hier nur noch Lv.1
                dups += 1                               # eine weitere Karte desselben Spielers: überspringen
                run += 1
                if run > MAX_DUP_RUN:
                    break                               # Ende der Liste (die Pfeile laufen nicht im Kreis)
                self.skip_card()
                continue
            run = 0
            seen.append(key)
            vals = {}
            if self.lv1_stop and cur != 'growth':       # die Stufe steht im Reiter Growth: dort zuerst nachsehen
                self.dev.tap(*TABS['growth'], TAB_NAME['growth'], wait=0.5)
                cur = 'growth'
            img, v = self.wait(lambda im: self.tab_value(cur, im), timeout=2.5, interval=0.3)
            if self.lv1_stop and cur == 'growth' and v is not None and v < self.min_level:
                # Nur Spieler ab einer Mindeststufe lesen (Emre, 11.11.2026; potbot.ini: min_level, 2 bis 70): Die Liste
                # ist nach Sternen und dann nach Stufe sortiert. Bei der ersten Karte unter der Mindeststufe ist Schluss;
                # sie wird nur notiert.
                if pid is not None:
                    self.lv1_cards.append({'id': pid, 'name': name, 'lv': v})
                if self.sort_level and not self.sorted_level:
                    # Liste nicht nach Level sortiert (Sortierung nicht gefunden): dann kommen danach noch höhere
                    # Karten, also diese überspringen statt aufzuhören (13.11.2026)
                    self.log(T('  {name} steht auf Lv.{lv} (unter {min}): übersprungen.',
                               name=name or raw.strip(), lv=v, min=self.min_level))
                    self.skip_card()
                    continue
                self.log(T('  {name} steht auf Lv.{lv} (unter {min}): hier hört der Scan der Spieler auf.',
                           name=name or raw.strip(), lv=v, min=self.min_level))
                break
            if v is None:                               # ein anderer Reiter offen?
                for t in TABS:
                    v = self.tab_value(t, img)
                    if v is not None:
                        cur = t
                        break
            if v is not None:
                vals[cur] = v
            ratings = [self.read_number(img, L.PS_RATING)]
            for t in [x for x in [cur] + TAB_NEXT[cur] if x not in vals]:
                self.dev.tap(*TABS[t], TAB_NAME[t], wait=0.5)
                img, v = self.wait(lambda im: self.tab_value(t, im), timeout=3.5, interval=0.3)
                cur = t
                if v is not None:
                    vals[t] = v
                ratings.append(self.read_number(img, L.PS_RATING))
            # das Rating steht auf jedem Reiter: am häufigsten gelesen, bei Gleichstand das längste
            ratings = [r for r in ratings if r]
            rating = max(ratings, key=lambda r: (ratings.count(r), len(str(r)))) if ratings else None
            p = {'id': pid, 'name': name if pid else raw.strip(), 'lv': vals.get('growth'), 'rating': rating,
                 'skills': vals.get('skills'), 'total': vals.get('reson'), 'stars': self.count_stars(img)}
            if pid is None:
                self.warn(T('Spieler nicht erkannt: {raw} (fehlt im Ergebnis)', raw=repr(raw.strip())))
                self.shot('name_unbekannt')
            elif None in (p['lv'], p['skills'], p['total']):
                self.warn(T('{name}: nicht alles lesbar (Stufe {lv}, Skills {skills}, Summe {total})',
                            name=name, lv=p['lv'], skills=p['skills'], total=p['total']))
                if getattr(self, 'debug_walk', False):
                    self.shot('unlesbar_' + re.sub(r'[^A-Za-z]+', '_', name or '')[:30])
            players.append(p)
            if len(players) % 10 == 0:
                self.log(T('  ... {n} Spieler', n=len(players)))
            # die Memory gleich mitlesen (1.15): „Zurück“ → in Manage Players ist dieser Spieler ausgewählt → „Memory“
            # öffnet Featured Memory genau bei ihm. Vorher suchte ein eigener Durchgang jeden Spieler über die
            # Laufschrift der Kacheln (23 Minuten für 47 Spieler, zwei nicht gefunden; Scan vom 11.11.2026).
            if pid is not None and self.mem_inline:
                try:
                    self.memory_inline(p)
                    cur = 'growth'
                except BotError as e:
                    self.mem_inline = False
                    self.shot('memory_direkt')
                    self.log(T('  Memory direkt lesen ging nicht ({err}); die Memories kommen danach im eigenen Durchgang.', err=e))
                    for _ in range(4):                  # zurück auf Player Stats dieses Spielers
                        kind = self.page()
                        self.zones(kind)
                        if kind == 'stats':
                            break
                        if kind == 'manage':
                            self.enter('stats', L.MP_GROWTH, 'Growth')
                            cur = 'growth'
                            break
                        self.dev.tap(*L.BACK, 'Zurück', wait=1.5)
                    else:
                        raise
            self.skip_card()                            # (auch wenn die nächste Karte ein Doppel ist)
        if dups:
            self.log(T('  {n} doppelte Karten übersprungen.', n=dups))

    def memory_inline(self, p):
        """Von Player Stats aus die Memory dieses Spielers lesen und wieder zurück auf Player Stats (Reiter Growth)."""
        self.back_to('manage')
        time.sleep(0.3)
        if self.player_here()[0] != p['id']:
            raise BotError(T('„Manage Players“ zeigt nicht {name}', name=p['name']))
        self._fm_before = None
        self.enter('memory', L.MP_MEMORY, 'Memory')
        mem, mid = self.fm_read_current(time.time() - 0.8)
        pos = (GAME['chars'].get(str(p['id'])) or {}).get('pos')
        mpos = (MEMS.get(mid) or {}).get('pos') if mid else None
        if mem['empty']:
            p['memory'] = None
            self.mem_done.add(p['id'])
        elif mid is None or mem['tier'] is None or (pos and mpos and pos != mpos):
            self.warn(T('{name}: Memory nicht sicher erkannt ({text}), bleibt im Simulator wie sie ist',
                        name=p['name'], text=(mem['frags'][0] if mem['frags'] else mem['eff'][:40])))
            self.mem_done.add(p['id'])
        else:
            p['memory'] = {'id': mid, 'tier': mem['tier']}
            self.mem_done.add(p['id'])
        self.back_to('manage')
        self.enter('stats', L.MP_GROWTH, 'Growth')
        time.sleep(0.3)
        if self.player_here()[0] != p['id']:
            raise BotError(T('Player Stats zeigt nicht mehr {name}', name=p['name']))

    def count_stars(self, img):
        """Sterne (Rang) eines Spielers auf Player Stats: die goldenen Kreise unter der Seltenheit (1 bis 6).
        Die Kreise berühren sich, darum zählt die Breite des goldenen Streifens geteilt durch den Abstand (34 in
        Referenz-Pixeln; gemessen am 09.11.2026). Nicht erreichte Sterne sind nicht golden und zählen nicht."""
        x0, y0, x1, y1 = self.px(L.PS_STARS, img)
        a = img[y0:y1, x0:x1]
        if a.size == 0:
            return None
        hsv = cv2.cvtColor(a, cv2.COLOR_BGR2HSV)
        m = (hsv[..., 0] >= 12) & (hsv[..., 0] <= 35) & (hsv[..., 1] >= 100) & (hsv[..., 2] >= 150)
        cols = np.where(m.sum(0) > max(3, 0.08 * m.shape[0]))[0]
        if not len(cols):
            return None
        pitch = L.PS_STAR_DX * img.shape[1] / REF_W
        n = int(round((cols[-1] - cols[0] + 1) / pitch))
        return n if 1 <= n <= 7 else None

    # ============================================================ Spieler: Memory (Featured Memory)
    def read_memory_panel(self, img):
        """Was rechts auf Featured Memory steht: Stücke des Namens, Wirkung, Stufe."""
        title = ' '.join(b.text for b in self.ocr(img, L.FM_TITLE)).strip()
        lines = [b.text for b in V.merge_lines(self.ocr(img, L.FM_TIER))]
        txt = ' '.join(lines)
        m = re.search(r'(?i)effects?\s*[:;：]', txt) or re.search(r'(?i)effects?', txt)
        flat = re.sub(r'[^a-z0-9/]', '', (txt + ' ' + ' '.join(b.text for b in self.ocr(img, L.FM_PANEL))).lower())
        # leerer Platz: "Lv.0/0", "Memory Tier I" und "No Memory Equipped" (Knopf Equip statt Replace)
        empty = (not title and not txt.strip()) or 'nomemoryequipped' in flat or 'lv0/0' in flat
        return {'frags': [title] if title else [], 'tier': None if empty else tier_of(lines),
                'eff': '' if empty else (txt[m.end():] if m else ''), 'empty': empty}

    def fm_tile_text(self, sy, tries=12):
        """Stufe und Namensstücke der ausgewählten Kachel (Featured Memory). Der Name läuft als Laufschrift durch;
        darum mehrmals lesen. Gibt (Stufe oder None, [(roher Text, normiert)])."""
        lv, frags = None, []
        for i in range(tries):
            if i:
                time.sleep(0.7)
            img = self.snap()
            ny = self.fm_selected_y(img)            # die Auswahl gleitet nach dem Antippen noch: jedes Mal neu suchen
            if ny is not None:
                sy = ny
            for b in self.ocr(img, (10, sy - 85, 260, sy + 85), upscale=1.8):
                m = re.search(r'(?i)\bl[vy]\s*\.?\s*(\d{1,2})\b', b.text)
                if m:
                    lv = int(m.group(1))
                n = label_name(b.text)
                if len(n) >= 4 and n not in ('mb', 'ws', 'op'):
                    frags.append((b.text, n))
            yield lv, frags

    def fm_match(self, frags, names, version, found):
        """Welcher Spieler passt zu den gelesenen Namensstücken? None, wenn keiner oder (noch) nicht eindeutig.
        Gezählt wird, wie viele Stücke zu einem Namen passen (ein unlesbares Stück schließt keinen aus; so fehlten
        Kuroo, Ushijima und Akaashi im Test vom 08.11.2026). Eine Version mit Zusatz zählt nur, wenn ein passendes
        Stück die Klammer zeigt; ein Name ohne Zusatz nur, wenn er ganz zu sehen war und nirgends eine Klammer stand."""
        if not frags:
            return None
        any_paren = any('(' in t or ')' in t for t, _ in frags)
        suffixes = self._fm_suffixes
        best = []
        for j, nm in enumerate(names):
            if not nm:
                continue
            hits = [(t, n) for t, n in frags if sub_score(n, nm) >= 0.8]
            if not hits:
                continue
            if version[j]:
                if not any('(' in t or ')' in t for t, _ in hits):
                    continue
                # Der Zusatz muss gut zu sehen sein, nicht nur sein Anfang: "(Winter" passt zu "(Winter Sports)" und
                # "(Winter Break)" (Hirugami falsch zugeordnet, Test 08.11.2026)
                suf = suffixes[j]
                if suffix_covered(suf, [n for _, n in frags]) < min(len(suf), 7):
                    continue
            elif any_paren or suffix_covered(nm, [n for _, n in hits]) < len(nm) - 1:
                # Name ohne Zusatz: über die Blicke zusammen ganz gelesen (die Laufschrift zeigt lange Namen nie auf
                # einmal; vorher fehlten so 24 von 46 Spielern, Scan 08.11.2026)
                continue
            best.append((len(hits), j))
        if not best:
            return None
        best.sort(reverse=True)
        top = [j for c, j in best if c == best[0][0]]
        if len({names[j] for j in top}) == 1:
            return top[0]
        return None

    def fm_read_current(self, t_tap):
        """Featured Memory: die Memory rechts (gehört zur ausgewählten Kachel). Gibt (mem, id oder None)."""
        panel = (L.FM_PANEL[0], L.FM_PANEL[1], L.FM_TIER[2], L.FM_TIER[3])
        time.sleep(max(0.0, 2.0 - (time.time() - t_tap)))   # das Panel baut sich verzögert auf
        # und manchmal noch später: bis zu 5 s darauf warten, dass es sich gegenüber vor dem Antippen geändert hat
        # (sonst las der Bot bei Sugawara noch die Memory von Nishinoya, Test 08.11.2026). Bleibt es gleich, tragen
        # beide dieselbe Memory oder keine.
        before = getattr(self, '_fm_before', None)
        if before is not None:
            while time.time() - t_tap < 5.0 and V.changed(before, self.snap(), panel) < 0.004:
                time.sleep(0.3)
        mem = self.read_memory_panel(self.settle(panel, timeout=2.0))
        mid = None
        if not mem['empty']:
            mid = match_memory_effect(mem['frags'], mem['eff'], mem['tier'])
            for _ in range(2):
                if mid is not None:
                    break
                time.sleep(0.8)
                t = ' '.join(b.text for b in self.ocr(self.snap(), L.FM_TITLE)).strip()
                if t:
                    mem['frags'].append(t)
                mid = match_memory_effect(mem['frags'], mem['eff'], mem['tier'])
        return mem, mid

    def fm_walk(self, players, on_player, part):
        """Geht die Liste unter Featured Memory Kachel für Kachel durch (siehe scan_player_memories) und ruft für jeden
        gesuchten Spieler einmal on_player(j, tile, t_tap) auf, solange er ausgewählt ist. Gibt (gefunden, Kacheln,
        names). on_player gibt zurück, was in gefunden[j] steht."""
        self.players_page('memory', L.MP_MEMORY, 'Memory')
        time.sleep(0.8)
        self._fm_before = None
        dbg = getattr(self, 'debug_walk', False)
        names = [norm(p.get('name') or '') if p.get('lv') is None or p['lv'] >= 2 else '' for p in players]
        version = ['(' in (p.get('name') or '') for p in players]
        self._fm_suffixes = [norm(re.sub(r'^[^(]*\(', '', p.get('name') or '')) if v else '' for p, v in zip(players, version)]
        skipped = len([p for p, nm in zip(players, names) if p.get('id') and not nm])
        if skipped:
            self.log(T('  {n} Spieler auf Lv.1: Memory nicht gesucht', n=skipped))
        todo = len([nm for nm in names if nm])
        found = {}
        panel = (L.FM_PANEL[0], L.FM_PANEL[1], L.FM_TIER[2], L.FM_TIER[3])
        dy = L.FM_LIST_DY
        x = L.FM_LIST_X
        sibling = [bool(nm) and not version[j] and any(version[k] and names[k].startswith(nm) for k in range(len(names)))
                   for j, nm in enumerate(names)]
        skip, last_tile = set(), 10 ** 6
        for pass_no in (1, 2):
            if pass_no == 2 and (len(found) >= todo or last_tile >= 10 ** 6):
                break
            if pass_no == 2:
                self.log(T('  Zweiter Durchgang für {n} fehlende Spieler ...', n=todo - len(found)))
            tiles = 0
            t_tap = time.time()
            try:
                if pass_no == 2:
                    # zweiter Durchgang nur für die noch fehlenden Spieler: Kacheln, die im ersten sicher jemand anderes
                    # waren, werden nur weitergeschaltet, die übrigen länger gelesen
                    self.players_page('memory', L.MP_MEMORY, 'Memory')
                    time.sleep(0.8)
                img = self.settle(panel, timeout=2.0)
                for _ in range(STEP_LIMIT):
                    sy = self.fm_selected_y(img)
                    if sy is None:
                        self.shot('memory')
                        raise BotError(T('Featured Memory: keine ausgewählte Kachel zu sehen'))
                    tiles += 1
                    if pass_no == 2 and tiles > last_tile:
                        break
                    # 1. Wer ist ausgewählt?
                    j, lv, frags = None, None, []
                    reader = iter(()) if (pass_no == 2 and tiles in skip) else self.fm_tile_text(sy, tries=12 if pass_no == 1 else 18)
                    for k_try, (lv_, frags) in enumerate(reader):
                        lv = lv_ if lv_ is not None else lv
                        if lv is not None and lv < 2:
                            break
                        j = self.fm_match(frags, names, version, found)
                        if j is not None and not version[j] and k_try < (7 if sibling[j] else 3):
                            # Name ohne Zusatz: erst nach mehreren Blicken ohne Klammer sicher; gibt es denselben Spieler
                            # auch mit Zusatz, eine ganze Runde der Laufschrift abwarten (sonst bekam "TORU OIKAWA" die
                            # Memory von "TORU OIKAWA (School Festival)", Scan 08.11.2026)
                            j = None                    # Name ohne Zusatz: erst nach drei Blicken ohne Klammer sicher
                        if j is not None:
                            break
                        # passt nach drei Blicken kein Stück zu einem gesuchten Namen: andere Karte, nicht länger warten
                        # (nur wenn schon genug zu lesen war, nicht nur "WAKI": unter der gerade ausgewählten Kachel fehlt der Name oft ein paar
                        # Sekunden ganz, Bild vom 08.11.2026)
                        if (pass_no == 1 and k_try >= 2 and sum(len(n) for _, n in frags) >= 12
                                and not any(nm and sub_score(n, nm) >= 0.8 for _, n in frags for nm in names)):
                            skip.add(tiles)
                            break
                    if j is not None:
                        skip.add(tiles)
                    if lv is not None and lv < 2:
                        last_tile = tiles - 1
                        if dbg:
                            self.log(f'    [memory] Kachel {tiles}: Lv.{lv}, Schluss')
                        break
                    # 2. was mit dem ausgewählten Spieler geschehen soll (lesen oder Memory wechseln)
                    if j is not None and j not in found:
                        found[j] = on_player(j, tiles, t_tap)
                        if len(found) % 10 == 0:
                            self.log(T('  ... {n} Spieler', n=len(found)))
                        cur = self.snap()
                        ny = self.fm_selected_y(cur)          # nach einem Wechsel neu nach der Auswahl sehen
                        if ny is None:
                            self.players_page('memory', L.MP_MEMORY, 'Memory')
                            time.sleep(0.8)
                            raise BotError(T('Featured Memory: nach dem Wechsel die Auswahl verloren'))
                        sy = ny
                    elif dbg:
                        if not frags and not (pass_no == 2 and tiles in skip):
                            self.shot(f'memory_leer_{tiles}')
                        self.log(f'    [memory] Kachel {tiles}: Lv.{lv}, '
                                 f'{"schon gelesen" if j is not None else "kein gesuchter Spieler"} {[t for t, _ in frags][:8]}')
                    if len(found) >= todo:
                        break
                    # 3. Liste so schieben, dass die Auswahl oben steht, dann die nächste Kachel antippen
                    if sy > L.FM_LIST[1] + 1.6 * dy:
                        before = self.snap()
                        ya = sy + 20
                        self.dev.swipe(x, ya, x, ya - (sy - (L.FM_LIST[1] + 0.6 * dy)), ms=2000, wait=0.5)
                        after = self.settle(L.FM_LIST)
                        sy2 = self.fm_selected_y(after)
                        if sy2 is not None:
                            sy = sy2
                        elif self.moved(before, after, L.FM_LIST) > 15:
                            raise BotError(T('Featured Memory: Auswahl beim Blättern verloren'))
                    ok = False
                    for attempt in range(4):
                        if attempt == 2:
                            # klappt nicht: Liste eine Kachel weiterschieben und die Auswahl neu suchen
                            self.dev.swipe(x, sy + 20, x, sy + 20 - dy, ms=2000, wait=0.5)
                            ny = self.fm_selected_y(self.settle(L.FM_LIST))
                            if ny is not None:
                                sy = ny
                        before = self.snap()
                        self._fm_before = before
                        self.dev.tap(x, sy + dy, 'nächster Spieler in der Liste', wait=0.6)
                        t_tap = time.time()
                        t0 = time.time()
                        while time.time() - t0 < 3.0:
                            cur = self.snap()
                            ny = self.fm_selected_y(cur)
                            if ny is not None and abs(ny - (sy + dy)) < 0.35 * dy:
                                ok = True
                                break
                            if ny is not None and abs(ny - sy) > 0.35 * dy:
                                ok = True               # Liste ist beim Antippen mitgerutscht: neue Auswahl woanders
                                break
                            time.sleep(0.25)
                        if ok:
                            break
                    if not ok:
                        self.shot('memory')
                        raise BotError(T('Featured Memory: nächste Kachel ließ sich nicht auswählen'))
                    img = cur
            except BotError as e:
                self.warn(T('{part}: abgebrochen ({err}); was bis dahin gelesen ist, bleibt', part=part, err=e))
        return found, tiles, names

    def scan_player_memories(self, players):
        """Featured Memory: Die Liste links ist nach Stufe sortiert (nicht wie Manage Players) und enthält jede Karte
        einzeln. Der Bot geht sie Kachel für Kachel durch: die nächste unter der ausgewählten antippen, prüfen, dass
        die Auswahl wirklich eine weiter ist (goldener Rahmen), Name der ausgewählten Kachel lesen, Memory rechts lesen.
        Schluss bei der ersten Karte auf Lv.1 (die tragen keine Memory; Emre, 08.11.2026)."""
        self.log(T('Spieler: Memories ...'))
        dbg = getattr(self, 'debug_walk', False)

        def read(j, tile, t_tap):
            mem, mid = self.fm_read_current(t_tap)
            if dbg:
                self.log(f'    [memory] Kachel {tile}: {players[j].get("name")} -> '
                         f'{"keine" if mem["empty"] else (mem["frags"][:1], mem["tier"], mid)}')
            return ('', mem, mid)

        found, tiles, names = self.fm_walk(players, read, T('Memories der Spieler'))
        todo = len([nm for nm in names if nm])
        n = 0
        for j, (lab, mem, mid) in sorted(found.items()):
            p = players[j]
            if not p.get('id'):
                continue
            if mem['empty']:
                p['memory'] = None
            elif mid is None or mem['tier'] is None:
                self.warn(T('{name}: Memory nicht sicher erkannt ({text}), bleibt im Simulator wie sie ist',
                            name=p['name'], text=(mem['frags'][0] if mem['frags'] else mem['eff'][:40])))
            else:
                p['memory'] = {'id': mid, 'tier': mem['tier']}
                n += 1
        missing = todo - len(found)
        if missing > 0:
            self.warn(T('Memories: {n} Spieler in der Liste nicht gefunden', n=missing))
            self.log('    ' + ', '.join(p.get('name') or '?' for j, (p, nm) in enumerate(zip(players, names))
                                      if nm and j not in found))
        self.log(T('  {n} Memories bei Spielern erkannt ({k} Kacheln angesehen).', n=n, k=tiles))

    # ============================================================ Memories anziehen (Featured Memory → Replace)
    def tap_word(self, x, y, words, purpose):
        """Einen Knopf antippen, dessen Wort sonst gesperrt ist (z. B. „Replace“): nur wenn genau dort eines dieser Wörter
        steht. Enhance, MAX und Ähnliches bleiben gesperrt."""
        img = self.snap()
        boxes = self.ocr(img, (x - 170, y - 50, x + 170, y + 50))
        here = [b for b in boxes if b.x0 - 12 <= x <= b.x1 + 12 and b.y0 - 30 <= y <= b.y1 + 30]
        if not any(norm(b.text) in words for b in here):
            self.shot('knopf')
            raise BotError(T('Der Knopf für „{purpose}“ heißt nicht {words} ({seen}).', purpose=purpose,
                             words=' / '.join(words), seen=', '.join(b.text for b in here) or '-'))
        from device import danger_text
        for b in here:
            if norm(b.text) not in words and danger_text(b.text):
                raise SafetyStop(T('Abgebrochen: An der Stelle für „{purpose}“ steht „{text}“.', purpose=purpose, text=b.text))
        self.dev.tap(x, y, purpose, wait=1.0, check=False)

    def fmr_open(self, timeout=6):
        """Ist die Auswahl nach „Replace“ offen (Reiter All, WS, MB ...)?"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            t = self.text_of(self.snap(), L.FMR_TABROW)
            if 'all' in t and ('ws' in t or 'mb' in t) and ('li' in t or 'op' in t):
                return True
            time.sleep(0.4)
        return False

    def fmr_read(self):
        """Die rechts ausgewählte Memory in der Auswahl: (id oder None, Stufe, Name)."""
        img = self.settle(L.FMR_EFF, timeout=1.5)
        title = ' '.join(b.text for b in self.ocr(img, L.FMR_TITLE)).strip()
        tier = tier_of([b.text for b in self.ocr(img, L.FMR_TIER)])
        txt = ' '.join(b.text for b in V.merge_lines(self.ocr(img, L.FMR_EFF)))
        m = re.search(r'(?i)effects?\s*[:;：]', txt) or re.search(r'(?i)effects?', txt)
        eff = txt[m.end():] if m else txt
        if not title and len(eff) < 15:
            return None, None, ''
        return match_memory_effect([title] if title else [], eff, tier or 1), tier, title

    def fmr_worn(self, img, x, y):
        """Trägt jemand die Memory dieser Kachel? Dann sitzt oben rechts ein rundes Gesicht, das über den Rahmen ragt.
        Gemessen am Bild vom 09.11.2026 (Lauf „Peak“): Streifen rechts oben 0,24 bis 0,64 weiß mit Gesicht, 0,00 ohne."""
        x0, y0, x1, y1 = self.px((x + 48, y - 50, x + 53, y - 25), img)
        reg = img[max(0, y0):y1, max(0, x0):x1]
        if reg.size == 0:
            return False
        return float((reg.min(axis=2) > 190).mean()) > 0.10

    def lv1_sure(self, img, region):
        """Zweiter Blick, vergrößert: Steht in diesem Bereich wirklich „Lv.1“ (und keine andere Stufe)?"""
        lvs = [lv_label(b.text) for b in self.ocr(img, region, upscale=2.0)]
        lvs = [v for v in lvs if v is not None]
        return bool(lvs) and all(v == 1 for v in lvs)

    def fmr_levels(self, img):
        """Stufen der Kacheln in der Auswahl nach „Replace“, gelesen an den Schildern „Lv.N“: {(Reihe, Spalte): Stufe}."""
        out = {}
        for b in self.ocr(img, (L.FMR_GRID[0], L.FMR_GRID[1], L.FMR_GRID[2], L.FMR_GRID[3] + 40)):
            lv = lv_label(b.text)
            if lv is None:
                continue
            c = min(range(len(L.FMR_COLS)), key=lambda i: abs(L.FMR_COLS[i] - b.cx))
            r = min(range(len(L.FMR_ROWS)), key=lambda i: abs(L.FMR_ROWS[i] - b.cy))
            if abs(L.FMR_COLS[c] - b.cx) < 70 and abs(L.FMR_ROWS[r] - b.cy) < 80:
                out[(r, c)] = lv
        return out

    def fmr_lv1_from(self, img, lvs, r, c):
        """Beginnen bei Kachel (r, c) die Memories auf Lv.1? Die Auswahl ist nach Stufe sortiert (Emre, 11.11.2026):
        ab der ersten Lv.1 kommen nur noch Lv.1, die sucht der Bot nicht mehr durch. Zur Sicherheit müssen alle
        Schilder dahinter auf dieser Seite auch Lv.1 zeigen; steht keins dahinter, liest er das Schild noch einmal groß."""
        if lvs.get((r, c)) != 1:
            return False
        rest = [v for (rr, cc), v in lvs.items() if (rr, cc) > (r, c)]
        if rest:
            return all(v == 1 for v in rest)
        x, y = L.FMR_COLS[c], L.FMR_ROWS[r]
        return self.lv1_sure(img, (x - 72, y - 75, x + 72, y + 80))

    def fmr_page_down(self):
        """Auswahl drei Reihen weiter (auf einer Kachel anfangen, ohne Nachlaufen). Gibt zurück, wie viele Reihen oben
        schon auf der vorigen Seite standen (1 nach vollen drei Reihen, mehr am Ende der Liste); None: Liste zu Ende."""
        before = self.snap()
        x = L.FMR_COLS[2]
        if not self.dev.sdrag(x, L.FMR_ROWS[3], x, L.FMR_ROWS[0], ms=900, hold=0.4, wait=0.6):
            self.dev.swipe(x, L.FMR_ROWS[3], x, L.FMR_ROWS[0], ms=2000, wait=0.6)
        after = self.settle(L.FMR_GRID)
        if self.moved(before, after, L.FMR_GRID) < 15:
            return None
        pitch = L.FMR_ROWS[1] - L.FMR_ROWS[0]
        d, sure = self.shift(before, after, L.FMR_GRID, 1, expect=3 * pitch)
        k = round(abs(d) / pitch) if d is not None else 3     # (bei gleich aussehenden Reihen: die Lösung nahe 3 Reihen)
        if not 1 <= k <= 3 or abs(abs(d or 3 * pitch) - k * pitch) > 25:
            k = 3
        return 4 - k

    def fmr_to_page(self, page):
        """Auswahl zurück an den Anfang und page Seiten weiter. False, wenn die Liste vorher endet."""
        self.dev.tap(*L.FMR_TABS['All'], T('Reiter {tab}', tab='All'), wait=1.2)
        x = L.FMR_COLS[2]
        self.top(x, L.FMR_ROWS[0] + 10, x, L.FMR_ROWS[3] - 10, region=L.FMR_GRID)
        for _ in range(page):
            if self.fmr_page_down() is None:
                return False
        return True

    def fmr_pick(self, mid, tier, free_only=False, _again=False):
        """In der Auswahl eine Kachel mit dieser Memory und Stufe antippen. Zuerst eine freie Kopie (ohne Gesicht oben
        rechts, trägt also niemand); ohne freie Kopie, wenn free_only False ist, die erste getragene.
        Gibt (Ergebnis, Kopien): Ergebnis 'frei', 'getragen' oder None (nicht ausgewählt); Kopien = gesehene Kopien.

        Immer der Reiter All, von links nach rechts (Emre, 11.11.2026). Der Bot merkt sich in self._fmr, welche Memory
        auf welcher Kachel liegt (1.12): beim nächsten Spieler geht er direkt zur gemerkten Kachel und liest nur dort
        weiter, wo er noch nicht war. Jede gemerkte Kachel prüft er nach dem Antippen; stimmt sie nicht mehr (die Liste
        hat sich verschoben), liest er die Auswahl einmal neu. Reihen, die nach dem Weiterschieben noch von der vorigen
        Seite zu sehen sind, liest er nicht doppelt (vorher zählte er solche Kopien zweimal)."""
        m = getattr(self, '_fmr', None)
        if m is None:
            m = self._fmr = {'tiles': [], 'done': False, 'page': 0, 'pos': 0, 'skip': 0, 'lv1': False}
        self.fmr_last = None
        self.dev.tap(*L.FMR_TABS['All'], T('Reiter {tab}', tab='All'), wait=1.2)
        at = [0]                                  # Seite, die gerade zu sehen ist (frisch geöffnet: der Anfang)
        n_tiles = len(L.FMR_ROWS) * len(L.FMR_COLS)

        def copies():
            return len([e for e in m['tiles'] if e['mid'] == mid and e['tier'] == tier])

        def known(worn):
            return [e for e in m['tiles'] if e['mid'] == mid and e['tier'] == tier and e['worn'] == worn]

        def to_page(page):
            if page < at[0]:
                ok = self.fmr_to_page(page)
            else:
                ok = all(self.fmr_page_down() is not None for _ in range(page - at[0]))
            at[0] = page
            return ok

        def take(e):
            """Zur gemerkten Kachel, antippen, prüfen."""
            if not to_page(e['page']):
                return False
            x, y = L.FMR_COLS[e['c']], L.FMR_ROWS[e['r']]
            img = self.settle(L.FMR_GRID, timeout=1.5)
            worn = self.fmr_worn(img, x, y)
            self.dev.tap(x, y, T('Memory in der Auswahl ansehen'), wait=0.7)
            got, t, _ = self.fmr_read()
            if got == mid and t == tier and worn == e['worn']:
                self.fmr_last = e
                return True
            return False

        def again():
            """Das Gemerkte stimmt nicht mehr: einmal alles neu lesen."""
            if _again:
                return None, copies()
            self.log(T('    Auswahl: die gemerkte Kachel stimmt nicht mehr, lese die Auswahl neu'))
            self._fmr = None
            self.fmr_to_page(0)
            return self.fmr_pick(mid, tier, free_only, _again=True)

        # 1. eine gemerkte freie Kopie
        free = known(False)
        if free:
            if take(free[0]):
                self.log(T('    Auswahl: gemerkte Kachel direkt angetippt (Seite {p})', p=free[0]['page'] + 1))
                return 'frei', copies()
            return again()
        # 2. weiterlesen, wo der Bot noch nicht war
        if not m['done']:
            if not to_page(m['page']):
                return again()
            while True:
                img = self.settle(L.FMR_GRID, timeout=1.5)    # vor dem Antippen: Gesichter noch ohne Auswahl-Rahmen
                lvs = self.fmr_levels(img)
                empty, lv1 = 0, False
                for idx in range(m['pos'], n_tiles):
                    r, c = divmod(idx, len(L.FMR_COLS))
                    if r < m['skip']:
                        m['pos'] = idx + 1
                        continue                  # diese Reihe stand schon auf der vorigen Seite
                    x, y = L.FMR_COLS[c], L.FMR_ROWS[r]
                    if self.fmr_lv1_from(img, lvs, r, c):
                        lv1 = m['lv1'] = True     # ab hier nur noch Lv.1: Ende der Suche
                        self.log(T('    Auswahl: ab Seite {p}, Reihe {r}, Kachel {c} nur noch Lv.1, nicht weiter gesucht',
                                   p=m['page'] + 1, r=r + 1, c=c + 1))
                        break
                    worn = self.fmr_worn(img, x, y)
                    self.dev.tap(x, y, T('Memory in der Auswahl ansehen'), wait=0.7)
                    got, t, title = self.fmr_read()
                    m['pos'] = idx + 1
                    if got is None and not title:
                        empty += 1
                        continue
                    e = {'page': m['page'], 'r': r, 'c': c, 'mid': got, 'tier': t, 'worn': worn, 'lv': lvs.get((r, c))}
                    m['tiles'].append(e)
                    if got == mid and t == tier and not worn:
                        self.fmr_last = e
                        return 'frei', copies()
                if lv1 or empty >= 6 or m['page'] >= 9:
                    m['done'] = True              # Ende der Liste (oder der Memories über Lv.1)
                    break
                skip = self.fmr_page_down()
                if skip is None:
                    m['done'] = True              # Liste zu Ende
                    break
                m['page'] += 1
                m['pos'], m['skip'] = 0, skip
                at[0] = m['page']
            self.log(T('    Auswahl ganz gelesen: {n} Memories gemerkt', n=len(m['tiles'])))
        self.fmr_lv1_seen = m['lv1']
        # 3. keine freie Kopie: die erste getragene, wenn das erlaubt ist
        worn_ones = known(True)
        if not free_only and worn_ones:
            if take(worn_ones[0]):
                return 'getragen', copies()
            return again()
        return None, copies()

    def equip_memories(self, wants, dry=False, bot=None):
        """wants: [(Spieler-ID, Memory-ID, Stufe)]. Geht Featured Memory durch und zieht jedem Spieler seine Memory an
        („Replace“, dann die Memory in der Auswahl). Spieler, die sie schon tragen, bleiben, wie sie sind.
        Gibt [(Name, Status, Text)]."""
        players = [{'id': pid, 'name': NAMES.get(pid, str(pid)), 'lv': None} for pid, _, _ in wants]
        want = {pid: (mid, tier) for pid, mid, tier in wants}
        mname = lambda mid, tier: f"{(MEMS.get(mid) or {}).get('name', mid)} ({['I', 'II', 'III', 'IV', 'V'][max(1, min(5, tier or 1)) - 1]})"
        report = []
        # Die Auswahl nach „Replace“ zeigt auch im Reiter All nur die Memories für die Position des Spielers (gesehen
        # am 11.11.2026). Darum merkt sich der Bot die Auswahl je Position (1.15; vorher einmal für alle: er suchte
        # dann z. B. die Memory eines WS in der gemerkten Auswahl eines Liberos und fand sie nie).
        self._fmr = None
        self._fmr_pos = {}
        self.log(T('Memories: {n} Spieler ...', n=len(players)))
        # Wer in dieser Aufstellung welche Memory schon trägt oder bekommen hat. Will ein zweiter Spieler dieselbe
        # (gleiche Memory, gleiche Stufe), nimmt der Bot sie dem ersten nicht weg (Emre hat z. B. Block Boost:
        # Hirugami V nur einmal, der Simulator wollte sie Aone und Kuroo geben, 08.11.2026).
        taken = {}
        wanted_by = {}
        for pid, mid, tier in wants:
            wanted_by.setdefault((mid, tier), []).append(pid)

        def handle(j, tile, t_tap):
            p = players[j]
            mid, tier = want[p['id']]
            pos = (GAME['chars'].get(str(p['id'])) or {}).get('pos')
            mpos = (MEMS.get(mid) or {}).get('pos')
            if pos and mpos and pos != mpos:
                why = T('{new} ist eine Memory für {mpos}, der Spieler ist {pos}', new=mname(mid, tier), mpos=mpos, pos=pos)
                report.append((p['name'], 'fehlt', why))
                self.warn(f"{p['name']}: {why}")
                return 'fehlt'
            mem, cur = self.fm_read_current(t_tap)
            now = T('keine') if mem['empty'] else (mname(cur, mem['tier']) if cur else T('unbekannt'))
            if not mem['empty'] and cur == mid and mem['tier'] == tier:
                taken[(mid, tier)] = p['name']
                report.append((p['name'], 'schon', mname(mid, tier)))
                self.log(T('  {name}: trägt schon {mem}', name=p['name'], mem=mname(mid, tier)))
                return 'schon'
            others = [NAMES.get(q, str(q)) for q in wanted_by.get((mid, tier), []) if q != p['id']]
            # Sollen mehrere dieselbe Memory tragen, nimmt der Bot nur eine freie Kopie (ohne Gesicht oben rechts in der
            # Auswahl). Eine getragene könnte die eines anderen aus der Aufstellung sein. (1.8 wechselte dann gar nicht.)
            self.tap_word(*L.FM_BTN, ('replace', 'equip'), 'Replace')
            if not self.fmr_open():
                self.shot('replace')
                raise BotError(T('Die Auswahl nach „Replace“ ging nicht auf.'))
            self.fmr_lv1_seen = False
            self._fmr = self._fmr_pos.get(pos)
            try:
                res, copies = self.fmr_pick(mid, tier, free_only=bool(others))
            finally:
                self._fmr_pos[pos] = self._fmr
            if res is None:
                self.dev.tap(*L.BACK, 'Zurück', wait=1.5)
                self._fm_before = None
                if others and copies:
                    why = T('{new}: keine freie Kopie ({n} gesehen, alle getragen); soll auch {others} tragen',
                            new=mname(mid, tier), n=copies, others=', '.join(others))
                else:
                    why = T('{new} nicht in der Auswahl gefunden', new=mname(mid, tier))
                    if self.fmr_lv1_seen:
                        why += T(' (Memories auf Lv.1 nicht durchsucht)')
                report.append((p['name'], 'fehlt', why))
                self.warn(T('{name}: {why}; bleibt bei {now}', name=p['name'], why=why, now=now))
                return 'fehlt'
            src = T('freie Kopie') if res == 'frei' else T('einem anderen Spieler abgenommen')
            if dry:
                self.dev.tap(*L.BACK, 'Zurück', wait=1.5)
                self._fm_before = None
                taken[(mid, tier)] = p['name']
                report.append((p['name'], 'dry', T('{now} → {new} ({src})', now=now, new=mname(mid, tier), src=src)))
                self.log(T('  {name}: würde wechseln: {now} → {new} ({src})', name=p['name'], now=now, new=mname(mid, tier), src=src))
                return 'dry'
            self.shot('replace_vorher')
            self.tap_word(*L.FMR_BTN, ('replace', 'equip'), 'Replace')
            time.sleep(1.5)
            self.shot('replace_nachher')
            # eine Rückfrage (z. B. „trägt schon ein anderer Spieler“) mit Confirm / OK bestätigen
            for b in self.ocr(self.snap(), (300, 250, 1300, 850)):
                if norm(b.text) in ('confirm', 'ok', 'yes'):
                    self.tap_word(b.cx, b.cy, ('confirm', 'ok', 'yes'), 'Bestätigen')
                    time.sleep(1.5)
                    break
            if self.fmr_open(timeout=1.5):
                self.dev.tap(*L.BACK, 'Zurück', wait=1.5)
            self._fm_before = None
            mem2, cur2 = self.fm_read_current(time.time() - 2)
            if not mem2['empty'] and cur2 == mid and mem2['tier'] == tier:
                # Gedächtnis der Auswahl nachführen: die neue Kopie trägt jetzt jemand, die alte ist frei
                e = getattr(self, 'fmr_last', None)
                tiles = (getattr(self, '_fmr', None) or {}).get('tiles', [])
                if e is not None:
                    e['worn'] = True
                if cur and not mem['empty']:
                    for o in tiles:
                        if o is not e and o['mid'] == cur and o['tier'] == mem['tier'] and o['worn']:
                            o['worn'] = False
                            break
                taken[(mid, tier)] = p['name']
                report.append((p['name'], 'ok', mname(mid, tier)))
                self.log(T('  {name}: {now} → {new}', name=p['name'], now=now, new=mname(mid, tier)))
                return 'ok'
            report.append((p['name'], 'fehlt', T('nach dem Wechsel steht dort {got}', got=mname(cur2, mem2['tier']) if cur2 else T('etwas anderes'))))
            self.warn(T('{name}: nach dem Wechsel nicht {new}', name=p['name'], new=mname(mid, tier)))
            return 'fehlt'

        if bot is not None and bot.ring:
            return self.memories_direct(players, handle, report, bot)
        found, tiles, names = self.fm_walk(players, handle, T('Memories anziehen'))
        for j, p in enumerate(players):
            if j not in found:
                report.append((p['name'], 'fehlt', T('in der Liste unter Featured Memory nicht gefunden')))
        return report

    def memories_direct(self, players, handle, report, bot):
        """Memories ohne die Liste links auf Featured Memory zu durchsuchen (1.15): Der Bot geht mit den Pfeilen auf
        „Player Potential“ zum Spieler (dort steht der ganze Name), dann „Zurück“: In „Manage Players“ ist dieser Spieler
        ausgewählt, und „Memory“ öffnet Featured Memory genau bei ihm (gesehen am 11.11.2026). Vorher las der Bot die
        Laufschrift unter jeder Kachel (etwa 10 s je Kachel) und verwechselte dabei Sugawara mit Nishinoya."""
        def idx(j):
            i = bot.index_of(players[j]['id'])
            return (i is None, i or 0)
        for j in sorted(range(len(players)), key=idx):
            p = players[j]
            try:
                if bot.index_of(p['id']) is None:
                    raise BotError(T('Spieler im Spiel nicht gefunden'))
                bot.goto(p['id'])
                self.zones('pp')
                self.back_to('manage')
                time.sleep(0.4)
                if self.player_here()[0] != p['id']:
                    raise BotError(T('„Manage Players“ zeigt nicht {name}', name=p['name']))
                self._fm_before = None
                self.enter('memory', L.MP_MEMORY, 'Memory')
                handle(j, None, time.time())
            except BotError as e:
                self.shot('memory_fehler')
                report.append((p['name'], 'fehlt', str(e)))
                self.warn(f"{p['name']}: {e}")
            # zurück auf Player Potential (dort gehen die Pfeile weiter)
            try:
                for _ in range(5):
                    kind = self.page()
                    self.zones(kind)
                    if kind == 'pp':
                        break
                    if kind == 'manage':
                        self.enter('pp', L.MP_POTENTIAL, 'Potential')
                        break
                    self.dev.tap(*L.BACK, 'Zurück', wait=1.5)
                self.zones('pp')
                here = self.player_here()[0]
                i = bot.index_of(here) if here is not None else None
                if i is not None and i < 10 ** 6:
                    bot.pos = i
            except BotError as e:
                self.warn(T('Zurück zu „Player Potential“ ging nicht ({err})', err=e))
                break
        done = {r[0] for r in report}
        for p in players:
            if p['name'] not in done:
                report.append((p['name'], 'fehlt', T('nicht mehr drangekommen')))
        return report

    # ============================================================ Spieler: Potentials
    def scan_player_pots(self, players):
        self.log(T('Spieler: Potentials ...'))
        self.players_page('pp', L.MP_POTENTIAL, 'Potential')
        by_id = {p['id']: p for p in players if p.get('id')}
        seen, worn = [], []
        try:
            self.pots_loop(players, by_id, seen, worn)
        except BotError as e:
            self.shot('fehler')
            self.warn(T('{part}: abgebrochen ({err}); was bis dahin gelesen ist, bleibt', part=T('Potentials der Spieler'), err=e))
        self.log(T('  Potentials von {n} Spielern gelesen ({k} Teile).', n=len(seen), k=len(worn)))
        return worn

    def pots_loop(self, players, by_id, seen, worn):
        n_known = len([p for p in players if p.get('id')])
        run = 0
        for _ in range(STEP_LIMIT):
            if len(seen) >= self.max_players or self.all_seen(len(seen)):
                break
            if self.gallery_players is None and n_known and len(seen) >= n_known:
                break                                   # so viele wie auf Player Stats: der Rest sind Doppel
            img = self.stable_snap()
            pid, name, raw = self.player_here(img)
            key = self.key_of(pid, raw)
            if seen and same_key(key, seen[0]):
                break                                   # einmal herum
            if not self.rarity_ok(pid):
                break
            if self.lv1_stop and pid is not None and pid not in by_id:
                break                                   # erster Spieler unter der Mindeststufe: dahinter kommen nur noch die
            if self.lv1_stop and n_known and len(seen) >= n_known:
                break                                   # (ohne das liefe der Bot durch alle 1385 Karten)
            if any(same_key(key, s) for s in seen):
                run += 1                                # Doppel: überspringen
                if run > MAX_DUP_RUN:
                    break
                self.skip_card()
                continue
            run = 0
            seen.append(key)
            got, bad = [], []
            for s in SLOTS:
                if not self.slot_filled(img, s):
                    continue
                try:
                    pc = self.read_slot(s)
                except BotError as e:
                    self.to_pp()
                    bad.append(f'{s} ({e})')
                    continue
                if pc is None:
                    continue
                if pc.set and pc.main and not pc.problems:
                    got.append(pc)
                else:
                    bad.append(f'{s} ({"; ".join(pc.problems) or "?"})')
            if pid is not None:
                p = by_id.get(pid)
                if p is None:
                    p = by_id[pid] = {'id': pid, 'name': name}
                    players.append(p)
                p['pots'] = [piece_dict(pc) for pc in got]
                worn += got
                if bad:
                    self.warn(T('{name}: Potential nicht sicher lesbar auf Platz {slots}', name=name, slots=', '.join(bad)))
            if len(seen) % 10 == 0:
                self.log(T('  ... {n} Spieler', n=len(seen)))
            self.skip_card()                            # (auch wenn die nächste Karte ein Doppel ist)

    # ============================================================ Pots im Inventar
    def filter_legendary(self, status=None):
        """Filter des Inventars: alle Sets, nur Legendary; status 'locked' oder 'unlocked' (Reset stellt Unlocked ein,
        gesperrte Teile zeigt das Spiel nur mit Status Locked)."""
        time.sleep(1.0)                                  # die Liste kann vom Blättern noch nachlaufen
        ok = False
        for _ in range(3):                               # manchmal kommt der erste Tipp nicht an
            self.dev.tap(*L.INV_FILTER, 'Filter Potential', wait=1.2)
            img, ok = self.wait(lambda im: self.filter_open(im), timeout=4)
            if ok:
                break
        if not ok:
            self.shot('filter_nicht_offen')
            raise BotError(T('Das Filter-Fenster ging nicht auf'))
        self.dev.swipe(820, 320, 820, 650, ms=400, wait=0.8)
        self.dev.tap(*L.F_RESET, 'Filter zurücksetzen', wait=1.0)
        img = self.snap()
        leg = [b for b in self.ocr(img, L.F_PANEL) if b.n == 'legendary']
        x, y = (leg[0].cx, leg[0].cy) if leg else L.F_LEGENDARY
        self.dev.tap(x, y, 'Legendary', wait=0.8)
        if status:
            self.filter_status(status)
        img = self.snap()
        oks = [b for b in self.ocr(img, (900, 660, 1400, 790)) if b.n == 'ok']
        x, y = (oks[0].cx, oks[0].cy) if oks else L.F_OK
        self.dev.tap(x, y, 'Filter OK', wait=1.2)
        img, closed = self.wait(lambda im: not self.filter_open(im), timeout=5)
        if not closed:
            self.shot('filter_bleibt_offen')
            raise BotError(T('Das Filter-Fenster ging nicht zu'))
        self.filter_set = None

    def filter_status(self, status):
        """Im offenen Filter-Fenster Status Locked oder Unlocked wählen (tippt auf den runden Knopf links vom Wort,
        nie auf das Wort selbst)."""
        for attempt in range(3):
            img = self.snap()
            hit = [b for b in self.ocr(img, L.F_PANEL) if b.n == status]
            if hit:
                b = hit[0]
                self.dev.tap(b.x0 - 22, b.cy, 'Status ' + status, wait=0.8)
                return
            self.dev.swipe(820, 650, 820, 320, ms=500, wait=0.8)      # Status steht weiter unten im Fenster
        self.shot('filter_status')
        raise BotError(T('Im Filter steht kein Status „{s}“', s=status))

    def scan_inventory_pots(self, worn=()):
        self.log(T('Potentials im Inventar (nur Legendary auf +12) ...'))
        self.players_page('pp', L.MP_POTENTIAL, 'Potential')
        self.open_inventory()
        self.zones('inv')
        pieces = []
        for status in ('unlocked', 'locked'):
            self.filter_legendary(status)
            self.log(T('  Status {s}:', s='Locked' if status == 'locked' else 'Unlocked'))
            pieces += self.inventory_slots(worn)
        self.log(T('  {n} Teile im Inventar.', n=len(pieces)))
        return pieces

    def inventory_slots(self, worn=()):
        pieces = []
        pitch = L.INV_ROWS[1] - L.INV_ROWS[0]
        h = L.INV_CELL / 2 - 8
        # Auf einer Kachel wischen: zwischen zwei Spalten bewegt sich die Liste nicht (Test vom 07.11.2026),
        # und nur langsam (2 s); schnelle Wischer auf einer Kachel schieben sie nicht sauber.
        x_gap = L.INV_COLS[1]
        for slot in SLOTS:
            self.tab(slot)
            # Das Spiel zeigt erst 32 Teile und lädt den Rest erst, wenn man am Ende schnell weiterwischt
            # (Tests 07.11.2026). Darum erst schnell ans Ende (lädt alles), dann zurück an den Anfang, dann Reihe für Reihe.
            n = self.to_end(L.INV_LIST, x_gap, 640, 300, stop=self.low_visible)   # nur bis zu den ersten Teilen unter +12
            self.log(T('    Platz {slot}: Liste geladen ({n} Wischer)', slot=slot, n=n))
            self.to_end(L.INV_LIST, x_gap, 300, 640)
            time.sleep(0.6)
            sigs, got, n_bad, low, low_sigs, row_low = set(), [], [0], [0], set(), [False]

            def read(k, y):
                img = self.snap()
                cells = [x for x in L.INV_COLS if V.golden_ratio(img, (x - h, y - h, x + h, y + h)) > 0.15]
                if not cells:
                    return False
                ty = min(y, L.INV_LIST[3] - 45)
                for x in cells:
                    self.dev.tap(x, ty, 'Teil in der Liste ansehen', wait=0.35)
                    panels = self.read_popup(L.INV_PANELS, slot)
                    cand = [p for p in panels or [] if p.button == 'Equip']
                    if not cand:
                        # Fenster nicht aufgegangen oder nicht gelesen: noch einmal (sonst fehlte das Teil still)
                        if panels:
                            self.close_inv_popup()
                        time.sleep(0.8)
                        self.dev.tap(x, ty, 'Teil in der Liste ansehen', wait=0.6)
                        panels = self.read_popup(L.INV_PANELS, slot)
                        cand = [p for p in panels or [] if p.button == 'Equip']
                        if not cand:
                            n_bad[0] += 1
                            self.log(T('    Platz {slot}: Reihe {k}: ein Teil ließ sich nicht öffnen', slot=slot, k=k + 1))
                    if cand:
                        pnl = cand[-1]
                        pc = pnl.piece
                        if pc.set and pc.main and not pc.problems:
                            sig = pc.label()
                            if pc.lv != 12:
                                row_low[0] = True
                                if sig not in low_sigs:          # nur fertige Teile (+12) zählen
                                    low_sigs.add(sig)
                                    low[0] += 1
                            elif (k, x) not in sigs and not pnl.equipped:
                                # nach Platz in der Liste zählen, nicht nach Inhalt: zwei gleiche Teile sind zwei Teile
                                # (mit Inhalt fehlte in V und II je eines; Test 07.11.2026)
                                sigs.add((k, x))
                                got.append(pc)
                        else:
                            n_bad[0] += 1
                            self.log(T('    Platz {slot}: ein Teil nicht sicher lesbar ({why})', slot=slot,
                                       why='; '.join(pc.problems) or '?'))
                    if panels:
                        self.close_inv_popup()
                # Das Spiel sortiert die höchsten Stufen nach oben: nach dem ersten Teil unter +12 kommen keine +12 mehr.
                # (Gelesen am geöffneten Teil, nicht an der Zahl auf der Kachel: die ist am Rand oft abgeschnitten.)
                if row_low[0]:
                    return False
                return True

            self.walk(L.INV_LIST, L.INV_ROWS[0], pitch, 1, read, (x_gap, 620, x_gap, 320, 2000), drag=True)
            # Teile, die ein Spieler trägt, nicht doppelt zählen (falls das Inventar sie mit anzeigt)
            mine = [pc for pc in got if not any(same_piece(pc, w) for w in worn if w.slot == slot)]
            if len(mine) < len(got):
                self.log(T('    {n} davon tragen Spieler (nicht doppelt gezählt)', n=len(got) - len(mine)))
            if n_bad[0]:
                self.warn(T('Platz {slot}: {n} Teile im Inventar nicht sicher lesbar (fehlen im Ergebnis)', slot=slot, n=n_bad[0]))
            self.log(T('  Platz {slot}: {n} Teile', slot=slot, n=len(mine)))
            if low[0]:
                self.log(T('    {n} Teile unter +12 nicht gezählt', n=low[0]))
            pieces += [piece_dict(pc) for pc in mine]
        return pieces

    # ============================================================ Memories (Manage Inventory)
    def scan_memories(self):
        self.log(T('Memories im Inventar ...'))
        self.home()
        self.enter('manage_inv', L.HOME_INVENTORY, 'Inventory')
        self.dev.tap(*L.INV_MEM_TAB, 'Reiter Memory', wait=1.5)
        best, count, skipped, stop, lowrun, lv1 = {}, [0], [0], [False], [0], [False]
        self.mem_copies = copies = {}                    # (Memory, Stufe, Lv) -> Anzahl (1.12: für den Simulator)
        pitch = L.MI_ROWS[1] - L.MI_ROWS[0]
        x_gap = (L.MI_COLS[1] + L.MI_COLS[2]) // 2
        panel = (L.MI_NAME[0], L.MI_NAME[1], L.MI_TIER[2], L.MI_TIER[3])
        self.top(x_gap, 260, x_gap, 680, region=L.MI_GRID)

        def read(k, y):
            img = self.snap()
            labels = [b for b in self.ocr(img, (L.MI_GRID[0], y + 10, L.MI_GRID[2], min(y + 85, L.MI_GRID[3] + 40)))
                      if b.n.startswith('lv') or b.n.startswith('ly')]
            cells = [x for x in L.MI_COLS if any(abs(b.cx - x) < 65 for b in labels)]
            if not cells:
                return False
            ty = min(y - 25, L.MI_GRID[3] - 50)
            # Stufe jeder Kachel dieser Reihe (Schild „Lv.N“ darunter)
            lv_at = {}
            for x in cells:
                near = [b for b in labels if abs(b.cx - x) < 65 and lv_label(b.text) is not None]
                if near:
                    lv_at[x] = lv_label(min(near, key=lambda b: abs(b.cx - x)).text)
            for x in cells:
                # Die Liste ist nach Stufe sortiert (Emre, 11.11.2026): ab der ersten Memory auf Lv.1 kommen nur noch
                # Lv.1, die liest der Bot nicht. Zur Sicherheit müssen alle sichtbaren Schilder dahinter auch Lv.1 zeigen;
                # steht keins dahinter, liest er das Schild noch einmal groß.
                if lv_at.get(x) == 1:
                    rest = [lv_label(b.text) for b in self.ocr(img, (L.MI_GRID[0], y + 10, L.MI_GRID[2], L.MI_GRID[3] + 40))
                            if b.cy > y + 95 or b.cx > x + 65]
                    rest = [v for v in rest if v is not None]
                    if all(v == 1 for v in rest) if rest else self.lv1_sure(
                            img, (x - 72, y + 5, x + 72, min(y + 90, L.MI_GRID[3] + 40))):
                        lv1[0] = stop[0] = True
                        break
                before = self.snap()
                self.dev.tap(x, ty, 'Memory ansehen', wait=0.35)
                im = self.wait_change(before, panel, timeout=1.0)
                name = ' '.join(b.text for b in self.ocr(im, L.MI_NAME))
                tier = tier_of([b.text for b in self.ocr(im, L.MI_TIER)])
                mid = match_memory_name(name)
                count[0] += 1
                if mid is None or tier is None:
                    self.warn(T('Memory nicht erkannt: {name} (Stufe {tier})', name=repr(name.strip()), tier=tier))
                    continue
                if self.rarities and (MEMS.get(mid) or {}).get('rarity') not in self.rarities:
                    skipped[0] += 1                      # nur SP, UR und SSR
                    lowrun[0] += 1
                    if lowrun[0] >= 2:                   # das Spiel sortiert nach Seltenheit: ab hier nur noch SR und R
                        stop[0] = True
                        break
                    continue
                lowrun[0] = 0
                best[mid] = max(best.get(mid, 0), tier)
                key = (mid, tier, lv_at.get(x))
                copies[key] = copies.get(key, 0) + 1
                if count[0] % 50 == 0:
                    self.log(T('  ... {n} Memories', n=count[0]))
            return not stop[0]

        self.walk(L.MI_GRID, L.MI_ROWS[0], pitch, 1, read, (x_gap, 640, x_gap, 310, 2000))
        self.log(T('  {n} Memories angesehen, {k} verschiedene.', n=count[0], k=len(best)))
        many = sum(1 for (m_, t_) in {(k[0], k[1]) for k in copies} if sum(n for k, n in copies.items() if k[:2] == (m_, t_)) > 1)
        self.log(T('  Anzahl und Level je Memory stehen in der Scan-Datei ({n} Memories mehrfach).', n=many))
        if skipped[0]:
            self.log(T('  {n} Memories unter SSR nicht übernommen.', n=skipped[0]))
        if lv1[0]:
            self.log(T('  Ab der ersten Memory auf Lv.1 nicht weiter gelesen (Memories auf Lv.1 fehlen im Ergebnis).'))
        return best

    # ============================================================ Bonds (Gallery)
    def read_bond_info(self, img):
        """(Name, Stufe, Art, Mitglieder-Texte, Titelzeile) der offenen Seite Bond Info; None, solange nichts dasteht."""
        rows = V.merge_lines(self.ocr(img, L.BI_TITLE))
        if not rows:
            return None
        y0 = min(b.cy for b in rows)
        line = ' '.join(b.text for b in sorted([b for b in rows if abs(b.cy - y0) < 22], key=lambda b: b.x0))
        name, lv = bond_title(line)
        if len(norm(name)) < 3:
            return None
        right = self.ocr(img, L.BI_RIGHT)
        rn = ' '.join(b.n for b in right)
        if 'bondskilleffect' in rn or 'deploymentbond' in norm(line):
            kind = 'bonds'
        elif 'bondstatbuff' in rn or 'unlockbonus' in rn:
            kind = 'collections'
        else:
            kind = None
        members = [re.sub(r'(?i)unlock\s*bonus\s*:?', '', b.text) for b in right if 'unlockbonus' in b.n]
        members += [b.text for b in self.ocr(img, L.BI_CARDS) if len(b.n) >= 5 and 'bonus' not in b.n]
        return name, lv, kind, members, line

    def bond_loop(self, res, seen, arrow):
        """Auf Bond Info: liest den offenen Bond und geht mit dem Pfeil weiter, bis einer wiederkommt oder nichts mehr kommt.
        Gibt 'seen' (wieder angekommen) oder 'end' (Pfeil ohne Wirkung) zurück."""
        page = (20, 150, 1600, 820)
        img = self.snap()
        for _ in range(400):
            info = None
            for _ in range(6):
                info = self.read_bond_info(img)
                if info:
                    break
                time.sleep(0.4)
                img = self.snap()
            if not info:
                return 'end'
            name, lv, kind, members, line = info
            # 1.16: Gleichnamige Bonds (30 Namen gibt es zwei- oder dreimal) sind verschiedene Bonds. Bis 1.15 galt der
            # zweite als "schon gesehen", und der Scan der Schule hörte dort auf: 57 von 142 Bonds gelesen (Scan vom
            # 14.11.2026). Jetzt: der passendste nach den Mitgliedern; ist der schon gelesen und passt ein noch nicht
            # gelesener fast genauso gut, ist es dieser. Erst wenn kein gleichnamiger mehr offen ist, ist die Runde herum.
            cands = match_bond_all(bond_norm(name), bond_pools(kind), members) if len(norm(name)) >= 3 else []
            r = None
            for k, bid, f in cands:
                if (k, bid) not in seen and f >= cands[0][2] - 0.08:
                    r = (k, bid)
                    break
            if cands and r is None:
                return 'seen'
            key = r if r else ('?', norm(name))
            if key in seen:
                return 'seen'
            seen.add(key)
            if r is None:
                self.warn(T('Bond nicht erkannt: {t}', t=repr(line)))
            else:
                res[r[0]][r[1]] = lv or 0
            if len(seen) % 25 == 0:
                self.log(T('  ... {n} Bonds', n=len(seen)))
            before = img
            self.dev.tap(*arrow, 'nächster Bond' if arrow == L.BI_NEXT else 'voriger Bond', wait=0.5)
            img, ok = self.wait(lambda im: V.changed(before, im, page) > 0.05, timeout=4.5, interval=0.3)
            if not ok:
                return 'end'
            time.sleep(0.4)                       # die Seite baut sich auf
            img = self.snap()
        return 'end'

    def bond_cards(self, img):
        """Namen der Bonds auf den Fotos der Bond Gallery (Kästen)."""
        return [b for b in V.merge_lines(self.ocr(img, L.BG_CARDS)) if match_bond(bond_title(b.text)[0]) is not None]

    def bond_school(self, res, seen):
        """Bond Gallery: ersten Bond der gezeigten Schule öffnen und mit den Pfeilen durchgehen."""
        cards = self.bond_cards(self.snap())
        if not cards:
            return
        c0 = cards[0]
        self.enter('bond_info', (c0.cx, c0.cy - 60), 'Bond ansehen')
        start = self.read_bond_info(self.snap())
        how = self.bond_loop(res, seen, L.BI_NEXT)
        self.back_to('bond_gallery')
        if how != 'end' or not start:
            return
        # Die Pfeile laufen nicht im Kreis: vom selben Foto aus noch rückwärts
        self.enter('bond_info', (c0.cx, c0.cy - 60), 'Bond ansehen')
        r = match_bond(start[0], start[2], start[3])
        seen.discard(r if r else ('?', norm(start[0])))      # den Anfang noch einmal zulassen, dann rückwärts
        self.bond_loop(res, seen, L.BI_PREV)
        self.back_to('bond_gallery')

    def scan_bonds(self):
        self.log(T('Bonds ...'))
        self.home()
        img = self.enter('gallery', L.HOME_GALLERY, 'Gallery')
        t = ' '.join(b.text for b in self.ocr(img, L.GAL_PLAYER))
        m = re.search(r'(\d{1,3})\s*/\s*(\d{1,3})', t)
        if m:
            self.gallery_players = int(m.group(1))
        img = self.enter('bond_gallery', L.GAL_BOND, 'Bond')
        m = re.search(r'(\d{1,3})\s*/\s*(\d{1,3})', ' '.join(b.text for b in self.ocr(img, L.BG_UNLOCKED)))
        unlocked = int(m.group(1)) if m else None
        res = {'collections': {}, 'bonds': {}}
        seen = set()
        for _ in range(8):
            # die fünf Schulen unten der Reihe nach (die Pfeile auf Bond Info laufen vielleicht nur durch eine Schule)
            for x in L.BG_SCHOOL_X:
                self.dev.tap(x, L.BG_SCHOOL_Y, 'Schule auswählen', wait=1.5)
                self.bond_school(res, seen)
            before = self.snap()
            self.dev.swipe(1250, L.BG_SCHOOL_Y, 450, L.BG_SCHOOL_Y, ms=900, wait=0.3)
            after = self.settle(L.BG_SCHOOLS)
            if V.changed(before, after, L.BG_SCHOOLS) < 0.02:
                break                                        # keine weiteren Schulen
        n = len(res['collections']) + len(res['bonds'])
        self.log(T('  {n} Bonds gelesen ({c} Sammlungs-Bonds, {b} Team-Bonds).', n=n, c=len(res['collections']),
                   b=len(res['bonds'])))
        active = sum(1 for r in res.values() for v in r.values() if v)
        if unlocked and active < unlocked:
            self.warn(T('Bonds: {n} freigeschaltete gelesen, die Bond Gallery zählt {m}.', n=active, m=unlocked))
        return res['collections'], res['bonds']

    # ============================================================ Cheers (Team -> Cheer)
    def read_cheer_popup(self, img):
        name = ' '.join(b.text for b in self.ocr(img, L.TC_POP_NAME))
        boxes = V.merge_lines(self.ocr(img, L.TC_POP_TEXT))
        ya = next((b.cy for b in boxes if 'activestats' in b.n), None)
        yd = next((b.cy for b in boxes if 'deploymentstats' in b.n), None)
        act = ' '.join(b.text for b in boxes if ya is not None and ya < b.cy < (yd if yd else ya + 90)).lower()
        dep = ' '.join(b.text for b in sorted(boxes, key=lambda b: b.cy) if yd is not None and yd < b.cy < yd + 110).lower()
        a = {}
        for m in STAT_RX.finditer(act):
            a.setdefault(CHEER_STAT[m.group(1)], int(m.group(2)))
        m = re.search(r'stats?\s*by\s*(\d+(?:\.\d+)?)\D+?morale\s*by\s*(\d+(?:\.\d+)?)', dep)
        d = None
        if m:
            d = [float(m.group(1)), float(m.group(2))]
            d = [int(v) if v == int(v) else v for v in d]
        return name, a, d

    def scan_cheers(self):
        self.log(T('Cheers ...'))
        self.home()
        self.enter('team', L.HOME_TEAM, 'Team')
        self.enter('cheer', L.TL_CHEER, 'Cheer')
        time.sleep(0.8)
        rb = self.read_number(self.snap(), L.TC_RB)
        got, sats = {}, {}
        y = L.TC_CARD_Y
        self.top(L.TC_CARDS[0] + 150, y, L.TC_CARDS[2] - 100, y, n=2)

        def read(k, x):
            sat = self.saturation(self.snap(), (x - 85, L.TC_CARD_Y - 70, x + 85, L.TC_CARD_Y + 75))
            self.dev.tap(x, y, 'Cheer ansehen', wait=0.8)
            im, ok = self.wait(self.cheer_popup, timeout=3.5)
            if not ok:
                return False                                   # keine Karte mehr
            time.sleep(0.3)
            name, a, d = self.read_cheer_popup(self.snap())
            self.dev.tap(*L.TC_POP_CLOSE, 'Fenster schließen', wait=0.6)
            self.wait(lambda i: not self.cheer_popup(i), timeout=3)
            cid = match_cheer(name)
            if not cid:
                self.warn(T('Cheer nicht erkannt: {name}', name=repr(name.strip())))
                return True
            if cid not in got:
                got[cid] = {'id': cid, 'stars': cheer_stars(cid, a, d) if a else None, 'a': a, 'd': d}
                sats[cid] = sat
                if not a or not d:
                    self.warn(T('Cheer {name}: Werte nicht ganz lesbar', name=CHEERS[cid]['name']))
            return True

        pitch = (L.TC_CARD_X[-1] - L.TC_CARD_X[0]) / (len(L.TC_CARD_X) - 1)
        self.walk(L.TC_CARDS, L.TC_CARD_X[0], pitch, 0, read, (1200, y, 900, y, 2000), limit=60)
        # Karten, die dir nicht gehören ("Collected 26/27"): grau statt bunt
        t = ' '.join(b.text for b in self.ocr(self.snap(), L.TC_COLLECTED))
        m = re.search(r'(?i)collected\D{0,3}(\d{1,2})', t) or re.search(r'(\d{1,2})\s*/\s*\d{1,2}', t)
        if m and len(got) > int(m.group(1)) and sats:
            n_un = len(got) - int(m.group(1))
            med = statistics.median(sats.values())
            low = sorted(sats.items(), key=lambda kv: kv[1])[:n_un]
            if all(v < 0.6 * med for _, v in low):
                for cid, _ in low:
                    got[cid]['own'] = False
                    got[cid]['stars'] = None
            else:
                self.warn(T('Cheers: {n} davon hast du laut Spiel nicht; welche, war nicht sicher zu erkennen.', n=n_un))
        for c in got.values():
            if c['stars'] is None:
                del c['stars']
        self.log(T('  {n} Cheers gelesen.', n=len(got)))
        return {'list': list(got.values()), 'rb': rb}

    # ============================================================ Coaches (Team -> Coach)
    def hues(self, img, region, min_sat=90):
        """Farbtöne (OpenCV 0..180) der kräftig farbigen Pixel eines Bereichs."""
        x0, y0, x1, y1 = self.px(region, img)
        a = cv2.cvtColor(img[max(0, y0):y1, max(0, x0):x1], cv2.COLOR_BGR2HSV).reshape(-1, 3)
        return a[(a[:, 1] > min_sat) & (a[:, 2] > 90)][:, 0]

    def pa_icons(self, img):
        """Mitten der Positions-Kreise in der Liste der Positional Advantages, nur ganz sichtbare."""
        x0, y0, x1, y1 = self.px((L.CO_PA_ICON_X - 18, L.CO_PA[1], L.CO_PA_ICON_X + 18, L.CO_PA[3]), img)
        hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        rows = list(((hsv[..., 1] > 60) & (hsv[..., 2] > 90)).mean(1) > 0.08)
        sy = REF_H / img.shape[0]
        # Lücken im dünnen Ring schließen (bis 9 Ref-Pixel)
        gap = int(9 / sy)
        i = 0
        while i < len(rows):
            if not rows[i]:
                j = i
                while j < len(rows) and not rows[j]:
                    j += 1
                if 0 < i and j < len(rows) and j - i <= gap:
                    rows[i:j] = [True] * (j - i)
                i = j
            else:
                i += 1
        out, start = [], None
        for i, on in enumerate(list(rows) + [False]):
            if on and start is None:
                start = i
            elif not on and start is not None:
                a, b = start, i - 1
                start = None
                if b - a + 1 < 22 / sy:                      # zu klein: ein angeschnittener Kreis
                    continue
                if a <= 1 or b >= len(rows) - 2:             # stößt an den Rand der Liste
                    continue
                out.append(L.CO_PA[1] + (a + b) / 2 * sy)
        return out

    def read_pas(self, img):
        """Die ganz sichtbaren Positional Advantages von oben nach unten: [{'t', 'pos', 'v', 'sure', 'text'}]."""
        boxes = self.ocr(img, L.CO_PA)
        texts = [b for b in boxes if b.x0 >= L.CO_PA_TEXT_X - 6]
        icons_txt = [b for b in boxes if b.cx < L.CO_PA_DOT_X - 10]
        lines = sorted(V.merge_lines(texts), key=lambda b: b.cy)
        groups, cur = [], []
        for ln in lines:
            if cur and ln.y0 - cur[-1].y1 > 12:
                groups.append(cur)
                cur = []
            cur.append(ln)
        if cur:
            groups.append(cur)
        out = []
        for cy in self.pa_icons(img):
            g = [grp for grp in groups if min(b.y0 for b in grp) - 6 <= cy <= max(b.y1 for b in grp) + 6]
            if len(g) != 1:
                continue
            grp = g[0]
            top, bot = min(b.y0 for b in grp), max(b.y1 for b in grp)
            if abs((top + bot) / 2 - cy) > 9 or top < L.CO_PA[1] + 4 or bot > L.CO_PA[3] - 4:
                continue                                   # Text oben oder unten abgeschnitten
            text = ' '.join(b.text for b in grp)
            r = 22
            pos = None
            for b in icons_txt:
                if abs(b.cy - cy) < r and b.n in POS_TEXT:
                    pos = POS_TEXT[b.n]
            pos = pos or hue_name(self.hues(img, (L.CO_PA_ICON_X - 18, cy - 18, L.CO_PA_ICON_X + 18, cy + 18)), POS_HUE)
            tier = hue_name(self.hues(img, (L.CO_PA_DOT_X - 7, cy - 7, L.CO_PA_DOT_X + 7, cy + 7), 80), TIER_HUE)
            pa = match_pa(text, tier)
            out.append({'t': pa['t'] if pa else None, 'v': pa['v'] if pa else None, 'sure': bool(pa and pa['sure']),
                        'pos': pos, 'text': text, 'cy': cy})
        return out

    def read_coach(self, img=None):
        """Die Werte des rechts gezeigten Coaches; die Positional Advantages mit Blättern in ihrer Liste."""
        img = self.snap() if img is None else img
        name = ' '.join(b.text for b in self.ocr(img, L.CO_NAME))
        cid = coach_by_name(name)
        if cid is None:
            return None, name
        t = ' '.join(b.text for b in self.ocr(img, L.CO_LEVEL))
        m = re.search(r'(?i)l[vy]\s*\.?\s*(\d{1,2})', t)
        level = int(m.group(1)) if m and 1 <= int(m.group(1)) <= 15 else None
        rating = self.read_number(img, L.CO_RATING)
        t = ' '.join(b.text for b in self.ocr(img, L.CO_BASIC))
        m = re.search(r'(?i)stats?\D{0,4}(\d{2,3})', t) or re.search(r'(\d{2,3})', t)
        basic = int(m.group(1)) if m else None
        eg = ' '.join(b.text for b in V.merge_lines(self.ocr(img, L.CO_EG)))
        c = {'id': cid, 'name': COACHES[cid]['name'], 'lv': coach_rarity(cid, eg) or 3, 'level': level,
             'rating': rating, 'basic': basic, 'pas': []}
        n = min(5, level // 3) if level else None
        if n == 0:
            return c, name
        # Liste der Positional Advantages: erst ganz nach oben (sie bleibt sonst stehen, wo sie war), dann ans Ende
        x = (L.CO_PA[0] + L.CO_PA[2]) // 2
        for attempt in range(2):
            for _ in range(2):
                self.dev.swipe(x, L.CO_PA[1] + 30, x, L.CO_PA[3] - 10, ms=200, wait=0.3)
            a = self.read_pas(self.settle(L.CO_PA, timeout=1.5))
            top = not a or a[0]['cy'] < L.CO_PA[1] + 85      # die erste steht oben in der Liste
            if top:
                break
        b = []
        if n is None or len(a) < n:
            self.dev.swipe(x, L.CO_PA[3] - 30, x, L.CO_PA[1] + 30, ms=800, wait=0.3)
            b = self.read_pas(self.settle(L.CO_PA, timeout=1.5))
        pas, sure = merge_pas(a, b, n)
        bad = [p['text'][:50] for p in pas if not p['t'] or not p['pos'] or not p['sure']]
        why = []
        if not sure or (n is not None and len(pas) != n):
            why.append(T('{n} von {m} gelesen', n=len(pas) - len(bad), m=n if n is not None else '?'))
        if not top:
            why.append(T('Liste ließ sich nicht ganz nach oben blättern'))
        if bad:
            why.append(T('nicht lesbar: {text}', text=' / '.join(bad)))
        if why:
            self.warn(T('{name} (Lv {lv}): Positional Advantages nicht sicher gelesen ({why})', name=c['name'], lv=level,
                        why='; '.join(why)))
        c['pas'] = [{'t': p['t'], 'pos': p['pos'], 'v': p['v']} for p in pas if p['t'] and p['pos']]
        return c, name

    def owned(self, img):
        t = ' '.join(b.text for b in self.ocr(img, L.CO_OWNED))
        m = re.search(r'(\d{1,3})\s*$', t.strip()) or re.search(r'(\d{1,3})', t)
        return int(m.group(1)) if m else None

    def coach_type(self, done, out):
        """Die Exemplare des links gewählten Coaches lesen: alle ab Stufe 3 (mit Positional Advantages), sonst eins."""
        img = self.snap()
        first, name = self.read_coach_header(img)
        if first is None:
            self.warn(T('Coach nicht erkannt: {name}', name=repr(name.strip())))
            return None
        if first in done:
            return first
        done.add(first)
        x = (L.CO_GRID_X[0] + L.CO_GRID_X[1]) // 2
        self.top(x, L.CO_GRID[1] + 90, x, L.CO_GRID[3] - 60, n=2)    # die Liste bleibt sonst stehen, wo sie war
        n_owned = self.owned(self.snap())
        got = []

        def read(k, y):
            img = self.snap()
            cells = [cx for cx in L.CO_GRID_X if V.golden_ratio(img, (cx - 30, y - 30, cx + 30, y + 30)) > 0.04]
            if not cells:
                return False
            low = []
            for cx in cells:
                before = self.snap()
                self.dev.tap(cx, y + 4, 'Coach ansehen', wait=0.5)
                im = self.wait_change(before, (L.CO_NAME[0], L.CO_NAME[1], L.CO_BASIC[2], L.CO_BASIC[3]), timeout=1.2)
                c, nm = self.read_coach(im)
                if c is None or c['id'] != first:
                    self.warn(T('Coach nicht erkannt: {name}', name=repr(nm.strip())))
                    continue
                low.append((c['level'] or 0) < 3)
                # Stufe 1 und 2 (ohne Positional Advantages): nur eins, und nur wenn es kein höheres gibt
                if not low[-1] or not got:
                    got.append(c)
            return not (low and all(low))             # nach Stufe sortiert: eine Reihe nur mit niedrigen ist das Ende

        x = (L.CO_GRID_X[0] + L.CO_GRID_X[1]) // 2
        self.walk(L.CO_GRID, L.CO_GRID_Y0, L.CO_GRID_DY, 1, read, (x, L.CO_GRID[3] - 70, x, L.CO_GRID[1] + 200, 1500),
                  limit=60)
        got = [g for g in got if (g['level'] or 0) >= 3] or got
        self.log(T('  {name}: {n} gelesen ({m} im Spiel)', name=COACHES[first]['name'], n=len(got),
                   m=n_owned if n_owned is not None else '?'))
        out += got
        return first

    def read_coach_header(self, img):
        name = ' '.join(b.text for b in self.ocr(img, L.CO_NAME))
        return coach_by_name(name), name

    def scan_coaches(self):
        self.log(T('Coaches ...'))
        self.home()
        self.enter('team', L.HOME_TEAM, 'Team')
        self.enter('coach', L.TL_COACH, 'Coach')
        done, out = set(), []
        self.top(L.CO_TYPE_X, L.CO_TYPES[1] + 60, L.CO_TYPE_X, L.CO_TYPES[3] - 40, n=2)
        for y in L.CO_TYPE_Y:
            self.dev.tap(L.CO_TYPE_X, y, 'Coach auswählen', wait=1.2)
            self.coach_type(done, out)
        # weitere Coaches unten in der Liste: ans Ende schieben, dann von unten nach oben
        self.dev.swipe(L.CO_TYPE_X, L.CO_TYPES[3] - 60, L.CO_TYPE_X, L.CO_TYPES[1] + 60, ms=600, wait=0.3)
        self.settle(L.CO_TYPES, timeout=1.5)
        for y in L.CO_TYPE_LAST_Y:
            self.dev.tap(L.CO_TYPE_X, y, 'Coach auswählen', wait=1.2)
            before = len(done)
            cid = self.coach_type(done, out)
            if cid is not None and len(done) == before:
                break                                 # schon gelesen: weiter oben ist nichts Neues
        self.log(T('  {n} Coaches gelesen.', n=len(out)))
        return out

    # ============================================================ alles zusammen
    def part(self, title, fn, *args):
        """Ein Teil des Scans; ein Fehler darin hält die anderen Teile nicht auf (außer der Sicherheits-Stopp)."""
        try:
            return fn(*args)
        except (SafetyStop, KeyboardInterrupt):
            raise
        except BotError as e:
            self.shot('fehler')
            self.warn(T('{part}: abgebrochen ({err})', part=title, err=e))
            return None

    def run_scan(self, parts):
        t0 = time.time()
        d = self.data
        if self.rarities:
            # Der Simulator lässt dann Spieler und Memories anderer Seltenheit so, wie sie sind
            d['only'] = sorted(self.rarities, key=['SP', 'UR', 'SSR', 'SR', 'R', 'N'].index)
        worn = []
        # Fortsetzen eines früheren Scans (1.15): was die alte Datei schon hat, bleibt drin, und die Teile ihrer Spieler
        # zählen im Inventar nicht doppelt
        base = getattr(self, 'base', None)
        if base:
            for k, v in base.items():
                if k not in ('hfhscan', 'date', 'tool'):
                    d.setdefault(k, v)
            if 'players' not in parts:
                worn = [V.Piece(slot=q.get('slot'), set=q.get('set'), lv=q.get('lv'), main=q.get('main'), rolls=dict(q.get('rolls') or {}))
                        for p in base.get('players') or [] for q in (p.get('pots') or [])]
                self.log(T('  Setze den Scan vom {date} fort ({n} getragene Teile bekannt).', date=base.get('date', '?'), n=len(worn)))
        if 'players' in parts:
            players = self.part(T('Spieler'), self.scan_players)
            if players:
                d['players'] = [p for p in players if p.get('id')]
                if self.lv1_stop:
                    d['lv1'] = [c['id'] for c in self.lv1_cards]
                self.save(d)
                rest = [p for p in players if p.get('id') not in self.mem_done]
                if rest:
                    self.part(T('Memories der Spieler'), self.scan_player_memories, rest)
                else:
                    self.log(T('  Memories der Spieler: {n} gleich mitgelesen.', n=sum(1 for p in players if p.get('memory'))))
                d['players'] = [p for p in players if p.get('id')]
                self.save(d)
            players = players or []
            r = self.part(T('Potentials der Spieler'), self.scan_player_pots, players)
            worn = r or []
            if players:
                d['players'] = [p for p in players if p.get('id')]
                self.save(d)
        if 'pots' in parts:
            pots = self.part(T('Potentials im Inventar'), self.scan_inventory_pots, worn)
            if pots is not None:
                d['pots'] = pots
                self.save(d)
        if 'memories' in parts:
            mems = self.part(T('Memories'), self.scan_memories)
            if mems:
                d['memories'] = {str(k): v for k, v in sorted(mems.items())}
                # jede Kopie mit Stufe (Tier), Level und Anzahl (1.12): 'memories' bleibt wie bisher (höchste Stufe je
                # Memory), damit ältere Seiten die Datei weiter lesen
                d['memoryCopies'] = [{'id': mid, 'tier': tier, 'lv': lv, 'n': n} for (mid, tier, lv), n in
                                     sorted(getattr(self, 'mem_copies', {}).items(), key=lambda kv: (kv[0][0], -kv[0][1], -(kv[0][2] or 0)))]
                self.save(d)
        if 'bonds' in parts:
            r = self.part(T('Bonds'), self.scan_bonds)
            if r and (r[0] or r[1]):
                d['collections'] = {str(k): v for k, v in sorted(r[0].items())}
                d['bonds'] = {str(k): v for k, v in sorted(r[1].items()) if v}
                self.save(d)
        if 'cheers' in parts:
            r = self.part(T('Cheers'), self.scan_cheers)
            if r and r['list']:
                d['cheers'] = r
                self.save(d)
        if 'coaches' in parts:
            r = self.part(T('Coaches'), self.scan_coaches)
            if r:
                d['coaches'] = r
                self.save(d)
        n_pl = len(self.seen_players)
        if 'players' in parts and n_pl and self.gallery_players and n_pl < self.gallery_players and not self.rarities and not self.lv1_stop:
            self.warn(T('Gelesen wurden {n} Spieler, die Gallery zählt {g}. Ist in „Manage Players“ ein Filter an?',
                        n=n_pl, g=self.gallery_players))
        if self.sorted_rarity or self.sorted_level:
            try:                                         # Sortierung wieder wie vorher (Deploy)
                self.home()
                self.enter('manage', L.HOME_PLAYER, 'Player')
                self.sort_by('deploy')
            except BotError:
                pass
        try:
            self.home()
        except BotError:
            pass
        t = int(time.time() - t0)
        self.log(T('Scan fertig nach {m} min {s} s.', m=t // 60, s=t % 60))
        return d
