"""Der eigentliche Ablauf im Spiel: Spieler finden, Teile lesen, ausziehen und anziehen."""
from __future__ import annotations

import difflib
import os
import re
import time

import cv2

import layout as L
import vision as V
from device import SafetyStop
from plan import NAMES, Loadout
from texts import T
from vision import SLOTS, Piece, norm, piece_diff, same_piece


MAX_DUPS = 3         # bis zu drei gleiche Karten mit dem Pfeil überspringen (1.15: zwei hochgelevelte Karten desselben
                     # Spielers beenden die Suche nicht mehr; bei den Lv.1-Doppeln am Ende der Liste ist nach drei Schluss).
                     # 1.14 hatte 0: Bei 1385 Karten und 10 bis 28 Doppeln je
                     # Spieler dauerte das über eine Stunde; Spieler hinter den Doppeln sucht der Bot jetzt in Manage Players


class BotError(RuntimeError):
    pass


class SetAbsent(BotError):
    """Das Set steht in der Set-Auswahl des Filters gar nicht: im Inventar gibt es dazu gerade kein Teil."""
    pass


def color_dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def same_key(a, b) -> bool:
    """Zwei Einträge der Spielerliste: IDs müssen gleich sein, nicht erkannte Namen (Text) nur ähnlich."""
    if isinstance(a, int) or isinstance(b, int):
        return a == b
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.92


class Bot:
    def __init__(self, dev, reader, log=print, dry=False, max_players=300, debug_dir=None):
        self.dev = dev
        self.reader = reader
        self.log = log
        self.dry = dry
        self.max_players = max_players
        self.debug_dir = debug_dir
        dev.reader = reader
        self.names = {norm(n): (pid, n) for pid, n in NAMES.items()}
        self.filter_set = None        # Set, das gerade im Filter des Inventars steht
        # Spielerliste in der Reihenfolge der Pfeile: ID, oder 'name:...' bei einem nicht erkannten Namen
        self.ring = []
        self.pos = 0                  # Stelle des offenen Spielers in self.ring
        self.closed = None            # True: die Pfeile laufen im Kreis, False: die Liste hat Enden, None: unbekannt
        self.current = {}             # Spieler-ID -> {Platz: Piece|None}, was er im Spiel trägt
        self.changed = set()          # Spieler-IDs, bei denen etwas an- oder ausgezogen wurde
        self.report = []              # (Spieler, Platz, Status, Text)
        # Spieler, die die Pfeile nicht erreichen (sie stehen hinter doppelten Karten): der Bot sucht ihre Kachel in
        # „Manage Players“ und öffnet Player Potential direkt dort (1.14). nav: ein Scanner für die Wege dorthin.
        self.nav = None
        self.direct = {}              # Spieler-ID -> Bildschirm der Kachel-Liste, auf dem seine Kachel stand
        self.tile_cache = self.load_tiles()   # dasselbe aus früheren Läufen (cache_tiles.json), als Startpunkt der Suche
        self.off_ring = False         # True: Player Potential steht bei einem direkt geöffneten Spieler, nicht in self.ring
        self.from_first = False       # True: die Pfeile fangen bei der ersten Karte an (kein Weg zurück nötig)

    # ============================================================ Grundlagen
    TILE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_tiles.json')

    def load_tiles(self):
        try:
            import json
            with open(self.TILE_FILE, encoding='utf-8') as f:
                d = json.load(f)
            return {int(k): int(v) for k, v in d.items()}
        except Exception:
            return {}

    def save_tile(self, pid, n):
        self.tile_cache[pid] = n
        try:
            import json
            with open(self.TILE_FILE, 'w', encoding='utf-8') as f:
                json.dump({str(k): v for k, v in self.tile_cache.items()}, f)
        except Exception:
            pass

    def snap(self):
        return self.dev.screenshot()

    def ocr(self, img, region=None, upscale=1.0):
        return self.reader.read(img, region, upscale)

    def shot(self, tag):
        """Bildschirmfoto für die Fehlersuche speichern."""
        if not self.debug_dir or self.dev.last is None:
            return None
        os.makedirs(self.debug_dir, exist_ok=True)
        path = os.path.join(self.debug_dir, time.strftime('%H%M%S_') + tag + '.png')
        cv2.imwrite(path, self.dev.last)
        return path

    def wait(self, fn, timeout=6.0, interval=0.12):
        """Macht Bildschirmfotos, bis fn(bild) etwas liefert."""
        t0 = time.time()
        while True:
            img = self.snap()
            r = fn(img)
            if r:
                return img, r
            if time.time() - t0 > timeout:
                return img, None
            time.sleep(interval)

    def text_of(self, img, region):
        """Der Text eines Bereichs, Zeile für Zeile, Wörter einer Zeile ohne Leerzeichen ('mainstats')."""
        return ' '.join(b.n for b in V.merge_lines(self.ocr(img, region)))

    TITLE_LINE = (104, 40, 520, 95)      # nur die Schrift des Seitentitels (für das schnelle Lesen einer Zeile)

    def title(self, img):
        t = norm(V.read_line(self.reader, img, self.TITLE_LINE))      # schnell (1.15); unklar: wie bisher
        if not any(k in t for k in ('playerpotential', 'potentialinventory', 'manageplayers')):
            t = self.text_of(img, (90, 15, 620, 95))
        if 'playerpotential' in t:
            return 'pp'
        if 'potentialinventory' in t:
            return 'inv'
        if 'manageplayers' in t:
            return 'manage'
        return 'other'

    def note(self, player, slot, status, text=''):
        self.report.append((player, slot, status, text))
        self.log(f'    {slot}: {text or status}')

    # ============================================================ Seite "Player Potential"
    def match_name(self, text):
        """Bester Name aus der Liste, seine Ähnlichkeit und die des zweitbesten."""
        n = norm(text)
        if len(n) < 4:
            return None, 0.0, 0.0
        scores = sorted(((difflib.SequenceMatcher(None, n, key).ratio(), val) for key, val in self.names.items()),
                        key=lambda x: -x[0])
        (sc, best), sc2 = scores[0], scores[1][0]
        return best, sc, sc2

    def player_here(self, img=None):
        """(ID, Name, Text) des Spielers auf der Seite Player Potential; ID None, wenn unklar."""
        img = img if img is not None else self.snap()
        # erst schnell als eine Zeile (1.15), nur bei einem klaren Treffer; sonst wie bisher
        text = V.read_line(self.reader, img, L.PP_NAME).strip(' [](|')
        m, sc, sc2 = self.match_name(text)
        if m and sc >= 0.9 and sc - sc2 >= 0.04:
            return m[0], m[1], text
        boxes = self.ocr(img, L.PP_NAME)
        text = ' '.join(b.text for b in sorted(boxes, key=lambda b: b.x0))
        m, sc, sc2 = self.match_name(text)
        if m and (sc >= 0.86 or (sc >= 0.75 and sc - sc2 >= 0.08)):
            return m[0], m[1], text
        return None, text.strip(), text

    def popup_open(self, img, region=L.PP_POPUP):
        """Ist ein Teile-Fenster offen? Es hat 'Main Stats' und 'Sub Stats' (die Seite selbst nur 'Stat Buff').
        Achtung: Das Filter-Fenster hat beides auch, darum vorher filter_open() prüfen."""
        t = self.text_of(img, region)
        return 'mainstats' in t and 'substats' in t

    def to_pp(self):
        """Sorgt dafür, dass die Seite Player Potential offen ist (ohne Fenster darüber)."""
        for _ in range(6):
            img = self.snap()
            kind = self.title(img)
            if kind == 'pp':
                if self.popup_open(img):
                    self.dev.tap(*L.PP_CLOSE, 'Fenster schließen')
                    continue
                return img
            if kind in ('inv', 'other') and self.setpick_open(img):
                # Erst die Set-Auswahl über dem Filter schließen: ihr X liegt weiter links als das des Filters
                # (vorher tippte der Bot sechsmal auf das verdeckte X des Filters und blieb hängen, Läufe vom 12.11.2026)
                self.dev.tap(*L.SP_CLOSE, 'Set-Auswahl schließen', wait=0.8)
                continue
            if kind in ('inv', 'other') and self.filter_open(img):
                self.dev.tap(*L.F_CLOSE, 'Filter schließen')
                continue
            if kind == 'inv':
                if self.popup_open(img, L.INV_PANELS):
                    self.dev.tap(*L.INV_CLOSE, 'Fenster schließen')
                    continue
                self.dev.tap(*L.BACK, 'Zurück zu Player Potential', wait=1.5)
                continue
            if kind == 'manage':
                pot = [b for b in self.ocr(img, (20, 540, 260, 700)) if b.n == 'potential']
                x, y = (pot[0].cx, pot[0].cy - 40) if pot else (84, 600)
                self.dev.tap(x, y, 'Potential öffnen', wait=2.0)
                continue
            break
        self.shot('unbekannte_seite')
        raise BotError(T('Bitte im Spiel bei einem beliebigen Spieler die Seite „Player Potential“ öffnen '
                         '(Player → Spieler antippen → Potential) und dann neu starten.'))

    def slot_filled(self, img, slot, centers=L.PP_SLOT):
        x, y = centers[slot]
        r = (x - 40, y - 40, x + 40, y + 40)
        if V.golden_ratio(img, r) > 0.08:
            return True
        m = V.mean_color(img, r)
        if max(m) - min(m) < 28:
            return False        # grau: ein gesperrter Platz mit Schloss (Watari, SR: Plätze V und VI; 11.11.2026)
        return color_dist(m, L.EMPTY_SLOT_BGR) > 40

    def stable_snap(self, tries=6):
        """Bildschirmfoto, sobald der Kreis fertig aufgebaut ist (zweimal hintereinander dieselben Plätze belegt)."""
        last, img = None, None
        for _ in range(tries):
            img = self.snap()
            pat = tuple(self.slot_filled(img, s) for s in SLOTS)
            if pat == last:
                return img
            last = pat
            time.sleep(0.25)
        return img

    def step(self, direction=1):
        """Ein Pfeil weiter (1) oder zurück (-1). Gibt (ID, Name) zurück; None, wenn sich nichts ändert."""
        img = self.snap()
        pid0, _, raw0 = self.player_here(img)
        self.dev.tap(*(L.PP_NEXT if direction > 0 else L.PP_PREV), 'nächster Spieler' if direction > 0 else 'voriger Spieler', wait=0.6)

        def changed(im):
            pid, name, raw = self.player_here(im)
            if pid is not None and pid != pid0:
                return pid, name, raw
            if pid is None and raw and difflib.SequenceMatcher(None, norm(raw), norm(raw0)).ratio() < 0.8:
                return pid, name, raw
            return None
        img, r = self.wait(changed, timeout=4.0, interval=0.3)
        # Kein anderer Name: entweder das Ende der Liste oder dieselbe Karte noch einmal (doppelte Karten stehen
        # hintereinander und sehen gleich aus). Vorher hielt der Bot das erste Doppel für das Ende und fand Spieler
        # dahinter nie (Sakunami, Lauf „Test1“ vom 11.11.2026: nach gut 60 Karten „Ende der Liste“). Darum weiter
        # tippen, bis ein anderer Name kommt (1.13).
        dups = 0
        while not r and dups < MAX_DUPS:
            dups += 1
            self.dev.tap(*(L.PP_NEXT if direction > 0 else L.PP_PREV), 'nächster Spieler' if direction > 0 else 'voriger Spieler', wait=0.5)
            img, r = self.wait(changed, timeout=1.2, interval=0.3)
        if not r:
            return None
        if dups:
            self.log(T('    ({n} doppelte Karten übersprungen)', n=dups))
        time.sleep(0.25)         # die neue Seite baut sich noch auf
        pid, name, raw = self.player_here()
        return pid, name

    def read_popup(self, region, slot):
        """Wartet auf ein Teile-Fenster und liest es. Gibt eine Liste von Panels zurück."""
        def got(im):
            ps = V.parse_panels(self.ocr(im, region), slot)
            return ps if ps and any(p.button for p in ps) else None
        img, panels = self.wait(got, timeout=5.0)
        if not panels:
            return None
        # bei Lesefehlern noch einmal genauer hinsehen
        if any(p.piece.problems for p in panels):
            ps2 = V.parse_panels(self.ocr(img, region, upscale=1.8), slot)
            if ps2 and sum(len(p.piece.problems) for p in ps2) < sum(len(p.piece.problems) for p in panels):
                panels = ps2
        return panels

    def read_slot(self, slot):
        """Liest das Teil auf einem Platz der Seite Player Potential (None, wenn leer)."""
        panels = None
        for attempt in range(2):                  # ein zweiter Versuch, falls der erste Tipp in eine Animation fiel (1.15)
            self.dev.tap(*L.PP_SLOT[slot], f'Platz {slot} ansehen', wait=0.5 + 0.6 * attempt)
            panels = self.read_popup(L.PP_POPUP, slot)
            if panels:
                break
            img = self.snap()
            if self.title(img) == 'inv':          # der Platz war leer: das Inventar ging auf
                self.to_pp()
                return None
            if not self.slot_filled(img, slot):
                return None
        if not panels:
            self.shot(f'platz_{slot}_nicht_lesbar')
            raise BotError(T('Platz {slot}: kein Fenster erschienen', slot=slot))
        p = panels[0]
        if p.piece.problems:
            self.log(T('    Hinweis Platz {slot}: {problems}', slot=slot, problems='; '.join(p.piece.problems)))
        self.dev.tap(*L.PP_CLOSE, 'Fenster schließen', wait=0.4)
        self.wait(lambda im: not self.popup_open(im), timeout=3)
        return p.piece

    def read_all(self):
        img = self.stable_snap()
        out = {}
        for s in SLOTS:
            try:
                out[s] = self.read_slot(s) if self.slot_filled(img, s) else None
            except BotError as e:
                # ein Platz nicht lesbar: als leer weiter, nicht alle Potentials abbrechen (1.15). Angezogen wird ohnehin
                # nur, was im Inventar gefunden und danach geprüft ist.
                self.log(T('    Hinweis Platz {slot}: nicht lesbar ({err}), zählt als leer', slot=s, err=e))
                out[s] = None
                try:
                    self.to_pp()
                except BotError:
                    pass
        return out

    def unequip(self, slot, expect: Piece):
        """Zieht auf der Seite Player Potential das Teil eines Platzes aus, wenn es das erwartete ist."""
        self.dev.tap(*L.PP_SLOT[slot], f'Platz {slot} ansehen', wait=0.5)
        panels = self.read_popup(L.PP_POPUP, slot)
        if not panels:
            self.to_pp()
            return False
        p = panels[0]
        if not same_piece(expect, p.piece):
            self.log(T('    Platz {slot} trägt etwas anderes als erwartet ({diff})', slot=slot, diff=piece_diff(expect, p.piece)))
            self.dev.tap(*L.PP_CLOSE, 'Fenster schließen', wait=0.5)
            return False
        if p.button != 'Unequip' or not p.button_box or norm(p.button_box.text) != 'unequip':
            self.dev.tap(*L.PP_CLOSE, 'Fenster schließen', wait=0.5)
            raise BotError(T('Platz {slot}: Knopf „Unequip“ nicht gefunden', slot=slot))
        if self.dry:
            self.dev.tap(*L.PP_CLOSE, 'Fenster schließen', wait=0.5)
            return True
        # Der rote Knopf „Unequip“ im Fenster liegt je nach Platz woanders, bei manchen dort, wo im Inventar „Quick
        # Unequip“ sitzt (Sicherheits-Stopp bei Hirugami, Lauf „Test1“ vom 11.11.2026). Auf Player Potential gilt für
        # diesen einen Tipp darum nur die Sperre dieser Seite (ihr „Quick Unequip“ rechts und die Kauf-Knöpfe oben);
        # die Wort-Prüfung bleibt: getippt wird nur, wo genau „Unequip“ steht, nie auf „Quick Unequip“ (1.13).
        if self.title(self.snap()) != 'pp':
            raise BotError(T('Platz {slot}: Ausziehen nur auf „Player Potential“', slot=slot))
        old = self.dev.zones
        self.dev.zones = L.PP_ZONES
        try:
            self.dev.tap(p.button_box.cx, p.button_box.cy, 'Unequip', wait=1.0)
        finally:
            self.dev.zones = old
        img, ok = self.wait(lambda im: not self.popup_open(im) and not self.slot_filled(im, slot), timeout=5)
        if not ok:
            self.to_pp()
            img = self.snap()
            if self.slot_filled(img, slot):
                self.shot(f'unequip_{slot}_fehlgeschlagen')
                raise BotError(T('Platz {slot}: Ausziehen hat nicht geklappt', slot=slot))
        return True

    # ============================================================ Spielerliste (Pfeile)
    def key_of(self, pid, raw):
        return pid if pid is not None else 'name:' + norm(raw or '')

    def index_of(self, key):
        if key in self.direct:
            return 10 ** 6 + list(self.direct).index(key)      # direkt geöffnete Spieler kommen nach denen der Pfeile
        for i, k in enumerate(self.ring):
            if same_key(k, key):
                return i
        return None

    def move(self, d):
        """Ein Pfeil in Richtung d (1 vor, -1 zurück). Hält self.ring und self.pos aktuell.
        Gibt (ID, Name) zurück, None am Ende der Liste."""
        r = self.step(d)
        if r is None:
            self.closed = False          # ein Ende: die Liste läuft also nicht im Kreis
            return None
        pid, name = r
        k = self.key_of(pid, name)
        n = len(self.ring)
        exp = self.pos + d
        if self.closed and n:
            exp %= n
        if 0 <= exp < n:
            if same_key(self.ring[exp], k):
                self.pos = exp
            else:
                i = self.index_of(k)
                if i is not None:              # die Reihenfolge hat sich verschoben
                    self.pos = i
                else:                          # ein neuer Spieler mitten in der Liste
                    at = exp if d > 0 else self.pos
                    self.ring.insert(at, k)
                    self.pos = at
        else:
            i = self.index_of(k)
            if i is not None:
                if (d > 0 and i == 0) or (d < 0 and i == n - 1):
                    self.closed = True         # die Pfeile laufen im Kreis
                self.pos = i
            elif d > 0:
                self.ring.append(k)
                self.pos = n
            else:
                self.ring.insert(0, k)
                self.pos = 0
        return pid, name

    def goto_index(self, t):
        """Mit den Pfeilen zur Stelle t von self.ring (kürzester Weg, wenn die Liste im Kreis läuft)."""
        n = len(self.ring)
        target = self.ring[t]
        d = t - self.pos
        if self.closed and n:
            fw = d % n
            d = fw if fw <= n - fw else fw - n
        direction = 1 if d > 0 else -1
        for _ in range(abs(d)):
            if self.move(direction) is None:
                break
            if same_key(self.ring[self.pos], target):
                break

    def back_on_ring(self):
        """Nach einem direkt geöffneten Spieler zurück zur ersten Karte, dort fangen die Pfeile an."""
        self.nav.players_page('pp', L.MP_POTENTIAL, 'Potential')
        pid, name, raw = self.player_here()
        for i, k in enumerate(self.ring):
            if same_key(k, self.key_of(pid, raw)):
                self.pos = i
                break
        else:
            self.pos = 0
        self.off_ring = False

    def goto_direct(self, pid):
        """Player Potential eines Spielers über seine Kachel in Manage Players öffnen."""
        try:
            img = self.to_pp()
            if self.player_here(img)[0] == pid:
                return
        except BotError:
            pass
        n = self.nav.open_pp_for(pid, start=max(0, self.direct.get(pid, 0) - 1))
        if n is None:
            raise BotError(T('{name} nicht wiedergefunden', name=NAMES.get(pid, pid)))
        self.direct[pid] = n
        self.off_ring = True

    def goto(self, pid):
        """Geht zu einem Spieler: mit den Pfeilen, oder direkt über Manage Players (self.direct)."""
        if pid in self.direct:
            return self.goto_direct(pid)
        if self.off_ring:
            self.back_on_ring()
        i = self.index_of(pid)
        if i is None:
            raise BotError(T('{name} ist nicht in der gelesenen Spielerliste', name=NAMES.get(pid, pid)))
        # Weit zurück: Player Potential neu bei der ersten Karte öffnen (etwa 10 s) und vorwärts gehen, statt viele Karten
        # mit dem Pfeil zurück (2 s je Karte; nach Watari am Listenende brauchte der Weg zu Sugawara 2½ min, 11.11.2026)
        if (self.nav is not None and self.from_first and not self.closed and i < self.pos
                and (self.pos - i) > i + 6 and not same_key(self.ring[self.pos], pid)):
            self.nav.players_page('pp', L.MP_POTENTIAL, 'Potential')
            self.pos = 0
            here = self.player_here()[0]
            j = self.index_of(here) if here is not None else None
            if j is not None and j < 10 ** 6:
                self.pos = j
        if not same_key(self.ring[self.pos], pid):
            self.goto_index(i)
        here = self.player_here()[0]
        if here == pid:
            self.pos = self.index_of(pid)
            return
        # nicht dort, wo er sein sollte: erst in der Nähe suchen, dann die ganze Liste
        for d, limit in ((1, 12), (-1, 24)):
            for _ in range(limit):
                r = self.move(d)
                if r is None:
                    break
                if r[0] == pid:
                    return
        for d in (1, -1):
            limit = len(self.ring) + 5 if self.closed else self.max_players
            for _ in range(limit):
                r = self.move(d)
                if r is None:
                    break
                if r[0] == pid:
                    return
        # Mit den Pfeilen nicht zurückgefunden (hinter langen Reihen doppelter Karten hält der Bot das für das Ende der
        # Liste; Lauf „Power Attack“ vom 11.11.2026, nach der Suche bei Spielern außerhalb der Aufstellung): Player
        # Potential über Manage Players neu öffnen, dort steht die erste Karte, und von vorn zählen (1.15).
        if self.nav is not None and self.from_first:
            self.log(T('  {name}: mit den Pfeilen nicht zurückgefunden, fange bei der ersten Karte neu an ...', name=NAMES.get(pid, pid)))
            self.nav.players_page('pp', L.MP_POTENTIAL, 'Potential')
            self.off_ring = False
            self.pos = 0
            for _ in range(len(self.ring) + 10):
                if self.player_here()[0] == pid:
                    i = self.index_of(pid)
                    if i is not None:
                        self.pos = i
                    return
                if self.move(1) is None:
                    break
        raise BotError(T('{name} nicht wiedergefunden', name=NAMES.get(pid, pid)))

    def scan(self, lo: Loadout):
        """Geht mit den Pfeilen durch die Spieler und liest die Teile der Spieler der Aufstellung."""
        want = {p.id for p in lo.players}
        self.to_pp()
        pid, name, raw = self.player_here()
        self.ring, self.pos, self.closed = [self.key_of(pid, raw)], 0, None
        self.log(T('Suche die Spieler (Start bei {start}) ...', start=name or repr(raw)))
        if pid in want:
            self.log(T('  gefunden: {name}', name=name))
            self.current[pid] = self.read_all()
        for d in (1, -1):
            if want <= set(self.current) or self.closed:
                break
            if d < 0 and self.from_first:
                break                           # die Pfeile fingen bei der ersten Karte an: davor steht niemand
            if d < 0:
                self.log(T('  Ende der Liste erreicht, suche vom Start aus in der anderen Richtung weiter.'))
                self.goto_index(0)
            steps = 0
            while not want <= set(self.current) and steps < self.max_players:
                r = self.move(d)
                steps += 1
                if r is None:
                    break
                if self.closed:
                    self.log(T('  Einmal rundherum: {n} Spieler.', n=len(self.ring)))
                    break
                pid, name = r
                if pid in want and pid not in self.current:
                    self.log(T('  gefunden: {name}', name=name))
                    self.current[pid] = self.read_all()
                elif steps % 20 == 0:
                    self.log(T('  ... {n} Spieler angesehen', n=steps))
        # Wen die Pfeile nicht erreicht haben: seine Kachel in Manage Players suchen (1.14)
        if self.nav is not None:
            for p in lo.players:
                if p.id in self.current:
                    continue
                self.log(T('  {name}: mit den Pfeilen nicht erreicht, suche die Kachel in „Manage Players“ ...', name=p.name))
                try:
                    n = None
                    if p.id in self.tile_cache:        # erst dort, wo die Kachel beim letzten Mal stand
                        n = self.nav.open_pp_for(p.id, start=max(0, self.tile_cache[p.id] - 3), most=8)
                    if n is None:
                        n = self.nav.open_pp_for(p.id)
                    if n is not None:
                        self.save_tile(p.id, n)
                except BotError as e:
                    self.log(T('  Das ging nicht ({err}).', err=e))
                    n = None
                self.off_ring = True
                if n is None:
                    continue
                self.direct[p.id] = n
                self.log(T('  gefunden: {name}', name=p.name))
                self.current[p.id] = self.read_all()
        missing = [p.name for p in lo.players if p.id not in self.current]
        if missing:
            self.log(T('  Nicht gefunden: {names}', names=', '.join(missing)))
            self.log(T('  Steht einer davon im Spiel noch auf Lv.1? Dann level ihn hoch: Die Lv.1-Karten stehen ganz hinten '
                       'zwischen den Doppeln, dort sucht der Bot nicht.'))
            if self.closed and self.nav is None:
                self.log(T('  ACHTUNG: Die Pfeile im Spiel zeigen nur {n} Spieler, die gesuchten sind nicht dabei. Dann ist die '
                           'Seite nicht über „Manage Players“ geöffnet (z. B. aus der Aufstellung), dort ist ein Filter an, '
                           'oder du hast den Spieler nicht.', n=len(self.ring)))
        return missing

    # ============================================================ Inventar
    def filter_open(self, img):
        t = self.text_of(img, (180, 95, 1420, 230))
        return 'filterpotential' in t or 'filterrules' in t

    def setpick_open(self, img):
        """Ist die Set-Auswahl („Selected: n“ unten) über dem Filter offen?"""
        return 'selected' in self.text_of(img, (700, 680, 1000, 770))

    def close_filter_windows(self):
        """Set-Auswahl und Filter-Fenster schließen, ohne etwas zu übernehmen."""
        for _ in range(4):
            img = self.snap()
            if self.setpick_open(img):
                self.dev.tap(*L.SP_CLOSE, 'Set-Auswahl schließen', wait=0.8)
            elif self.filter_open(img):
                self.dev.tap(*L.F_CLOSE, 'Filter schließen', wait=0.8)
            else:
                break
        self.filter_set = None

    def open_inventory(self):
        self.dev.tap(*L.PP_INVENTORY, 'Potential Inventory', wait=1.2)
        img, ok = self.wait(lambda im: self.title(im) == 'inv', timeout=8)
        if not ok:
            self.shot('inventar_nicht_offen')
            raise BotError(T('Das Potential-Inventar ging nicht auf'))

    def tab(self, slot):
        self.dev.tap(*L.INV_TAB[slot], f'Reiter {slot}', wait=1.0)

    USE_MAIN_FILTER = False
    # Reihenfolge der Sets in der Set-Auswahl des Filters (immer gleich, gemessen am 11.11.2026)
    SET_ORDER = ['Assist Receive', 'Supreme Receive', 'Forceful Guard', 'Precise Serve', 'Precise Set', 'Power Vibe',
                 'Power Rise', 'Rapid Quick Attack', 'Sharp Sense', 'Precise Block', 'Block Movement', 'Morale Surge',
                 'Awareness EnH.', 'Setter Dump', 'Status EnH.']
    MAIN_FILTER = {'Power': 'poweratk', 'Quick': 'quickatk', 'Set': 'set', 'Serve': 'serve', 'Receive': 'receive',
                   'Block': 'block', 'Save': 'save', 'Awareness': 'awareness', 'Strength': 'strength', 'Reflex': 'reflex',
                   'Spirit': 'spirit', 'AtkTech': 'attacktechnique', 'DefTech': 'defensetechnique'}

    def set_filter(self, set_name, slot=None, main=None, status='locked'):
        """Filter: nur dieses Set, nur Legendary, dazu der Status (Locked oder Unlocked; nach „Reset“ steht er auf
        Unlocked, die Teile auf +12 sind aber meist gesperrt) und auf Platz II, IV und VI der Hauptwert. So zeigt die
        Liste nur noch wenige Teile, statt alle des Sets (vorher über 10 Minuten für ein Set, Test 08.11.2026)."""
        # Ohne Hauptwert (1.15): Set + Legendary + Status lässt je Reiter nur wenige Teile übrig, und derselbe Filter
        # gilt dann für alle Plätze und Spieler mit diesem Set. Vorher stellte der Bot den Filter für fast jedes Teil
        # neu ein (etwa 30 s je Teil, Läufe vom 11.11.2026).
        if not self.USE_MAIN_FILTER or slot not in ('II', 'IV', 'VI') or main not in self.MAIN_FILTER:
            main = None
        key = (set_name, slot if main else None, main, status)
        if self.filter_set == key:
            return
        self.log(T('    Filter: {set}, Legendary', set=set_name) + (f', {main}' if main else '') + f', {status}')
        self.dev.tap(*L.INV_FILTER, 'Filter Potential', wait=1.2)
        img, ok = self.wait(lambda im: self.filter_open(im), timeout=5)
        if not ok:
            self.shot('filter_nicht_offen')
            raise BotError(T('Das Filter-Fenster ging nicht auf'))
        # nach oben scrollen, damit "Set" sichtbar ist
        self.dev.swipe(820, 320, 820, 650, ms=400, wait=0.8)
        self.dev.tap(*L.F_RESET, 'Filter zurücksetzen', wait=1.0)
        self.filter_set = None
        # Die Knöpfe im Filter-Fenster stehen nach „Zurücksetzen“ immer an derselben Stelle: beim ersten Mal per
        # Texterkennung gesucht, danach gemerkt (1.15; spart je Filter mehrere Lesungen des ganzen Fensters). Die
        # Sicherheitsprüfung an jeder Tippstelle bleibt.
        fp = self.__dict__.setdefault('_fpos', {})
        if 'tts' not in fp:
            boxes = self.ocr(self.snap(), L.F_PANEL)
            tts = sorted([b for b in boxes if 'taptoselect' in b.n], key=lambda b: b.cy)
            if not tts:
                self.shot('filter_ohne_set')
                raise BotError(T('Im Filter steht kein „Tap to Select“'))
            fp['tts'] = (tts[0].cx, tts[0].cy)
            leg = [b for b in boxes if b.n == 'legendary']
            if leg:
                fp['legendary'] = (leg[0].cx, leg[0].cy)
        self.dev.tap(*fp['tts'], 'Set auswählen', wait=1.2)
        self.pick_set(set_name)
        # Legendary
        if 'legendary' not in fp:
            leg = [b for b in self.ocr(self.snap(), L.F_PANEL) if b.n == 'legendary']
            fp['legendary'] = (leg[0].cx, leg[0].cy) if leg else L.F_LEGENDARY
        self.dev.tap(*fp['legendary'], 'Legendary', wait=0.8)
        if main:
            self.pick_main(slot, main)
        self.pick_status(status)
        if 'ok' not in fp:
            oks = [b for b in self.ocr(self.snap(), (900, 660, 1400, 790)) if b.n == 'ok']
            fp['ok'] = (oks[0].cx, oks[0].cy) if oks else L.F_OK
        self.dev.tap(*fp['ok'], 'Filter OK', wait=1.2)
        img, closed = self.wait(lambda im: not self.filter_open(im), timeout=5)
        if not closed:
            self.shot('filter_bleibt_offen')
            raise BotError(T('Das Filter-Fenster ging nicht zu'))
        self.filter_set = key

    def pick_status(self, status):
        """Im offenen Filter: Status Locked oder Unlocked (runder Knopf links vom Wort, nie das Wort selbst)."""
        fp = self.__dict__.setdefault('_fpos', {})
        if 'status:' + status in fp:
            self.dev.tap(*fp['status:' + status], 'Status ' + status, wait=0.8)
            return
        for attempt in range(3):
            boxes = self.ocr(self.snap(), L.F_PANEL)
            hit = [b for b in boxes if b.n == status]
            if hit:
                if attempt == 0:                     # ohne Wischen gefunden: Stelle für beide Status merken
                    for b in boxes:
                        if b.n in ('locked', 'unlocked'):
                            fp['status:' + b.n] = (b.x0 - 22, b.cy)
                self.dev.tap(hit[0].x0 - 22, hit[0].cy, 'Status ' + status, wait=0.8)
                return
            self.dev.swipe(820, 650, 820, 320, ms=500, wait=0.8)      # Status steht weiter unten im Fenster
        self.shot('filter_status')
        raise BotError(T('Im Filter steht kein Status „{s}“', s=status))

    def pick_main(self, slot, main):
        """Im offenen Filter: Main Stats → „Tap to Select“ → Reiter „Position II/IV/VI“ → den Hauptwert → OK.
        Klappt das nicht, geht es ohne diesen Filter weiter (nur langsamer)."""
        want = self.MAIN_FILTER[main]
        boxes = self.ocr(self.snap(), L.F_PANEL)
        head = next((b for b in boxes if 'mainstat' in b.n), None)
        tts = sorted([b for b in boxes if 'taptoselect' in b.n and (head is None or b.cy > head.cy)], key=lambda b: b.cy)
        if head is None or not tts:
            self.log(T('    (Filter nach Hauptwert nicht gefunden, weiter ohne)'))
            return
        self.dev.tap(tts[0].cx, tts[0].cy, 'Hauptwert auswählen', wait=1.2)
        img, ok = self.wait(lambda im: any('position' in b.n for b in self.ocr(im, (200, 60, 1400, 260))), timeout=5)
        if not ok:
            self.shot('hauptwert_nicht_offen')
            self.log(T('    (Filter nach Hauptwert ging nicht auf, weiter ohne)'))
            return
        tab = next((b for b in self.ocr(img, (200, 60, 1400, 260)) if b.n == 'position' + slot.lower()), None)
        if tab:
            self.dev.tap(tab.cx, tab.cy, f'Position {slot}', wait=0.8)
        done = False
        for attempt in range(3):
            img = self.snap()
            hit = [b for b in self.ocr(img, (150, 200, 1450, 720)) if re.sub(r'pct$', '', b.n) == want]
            if hit:
                self.dev.tap(hit[0].cx, hit[0].cy, f'Hauptwert {main}', wait=0.8)
                done = True
                break
            self.dev.swipe(800, 600, 800, 350, ms=700, wait=0.8)   # „Defense Technique“ steht weiter unten
        oks = sorted([b for b in self.ocr(self.snap(), (200, 640, 1450, 880)) if b.n == 'ok'], key=lambda b: -b.cx)
        if oks:
            self.dev.tap(oks[0].cx, oks[0].cy, 'OK (Hauptwert)', wait=1.0)
        else:
            self.shot('hauptwert_ok')
            raise BotError(T('Im Fenster für den Hauptwert steht kein „OK“'))
        if not done:
            self.log(T('    (Hauptwert {main} nicht in der Liste, weiter ohne)', main=main))

    def pick_set(self, set_name):
        img, ok = self.wait(lambda im: any('selected' in b.n for b in self.ocr(im, (700, 680, 1000, 770))), timeout=5)
        if not ok:
            self.shot('setauswahl_nicht_offen')
            raise BotError(T('Die Set-Auswahl ging nicht auf'))

        def selected(im):
            t = ' '.join(b.text for b in self.ocr(im, (700, 680, 1000, 770)))
            d = [int(c) for c in t if c.isdigit()]
            return d[-1] if d else None
        if (selected(img) or 0) > 0:
            # Reste einer alten Auswahl: zurücksetzen
            self.dev.tap(*L.SP_RESET, 'Set-Auswahl zurücksetzen', wait=0.8)
        down, stuck, prev = True, 0, None
        # Die Liste steht beim Öffnen immer oben und hat immer dieselbe Reihenfolge (gemessen am 11.11.2026). Für Sets
        # weiter unten blättert der Bot erst ohne hinzusehen in die Nähe, für die letzten ganz ans Ende (1.15; vorher las
        # er nach jedem Wischer die ganze Liste, bis zu 7 Mal für „Status EnH.“).
        idx = self.SET_ORDER.index(set_name) if set_name in self.SET_ORDER else None
        if idx is not None and idx >= 11:
            for _ in range(3):
                self.dev.swipe(800, 600, 800, 230, ms=120, wait=0.25)
            time.sleep(0.6)
            down = False
        elif idx is not None and idx >= 3:
            for _ in range((idx - 1) // 2):
                self.dev.swipe(800, 560, 800, 300, ms=500, wait=0.6)
        for attempt in range(40):
            img = self.snap()
            boxes = self.ocr(img, L.SP_LIST)
            hit = [b for b in boxes if V.set_from_text(b.text) == set_name]
            if getattr(self, 'debug_sets', False):
                self.log(f'      [set] {attempt} {"ab" if down else "auf"}: ' + ', '.join(f'{V.set_from_text(b.text)}@{b.cy:.0f}' for b in boxes if V.set_from_text(b.text)))
            if not hit and idx is not None and attempt == 0:
                seen = [self.SET_ORDER.index(V.set_from_text(b.text)) for b in boxes if V.set_from_text(b.text) in self.SET_ORDER]
                if seen and min(seen) > idx:
                    down = False                     # zu weit geblättert: zurück nach oben
                elif seen and max(seen) < idx:
                    down = True
            if hit:
                # ohne Wort-Prüfung: hier steht ein Set-Name, und „Status Enhancement“ liest die Texterkennung auch als
                # zwei Stücke, von denen „Enhancement“ sonst als gesperrter Knopf gilt (Test 08.11.2026)
                self.dev.tap(hit[0].cx, hit[0].cy, f'Set {set_name}', wait=0.8, check=False)
                if selected(self.snap()) != 1:
                    self.dev.tap(hit[0].cx, hit[0].cy, f'Set {set_name}', wait=0.8, check=False)
                break
            # hat der letzte Wischer nichts bewegt? Zweimal hintereinander: Ende der Liste (1.15: das Bild dieser Runde
            # ist zugleich die Kontrolle des Wischers davor, das spart ein Bildschirmfoto je Runde)
            if prev is not None and V.changed(prev, img, L.SP_LIST) < 0.004:
                stuck += 1
                if stuck >= 2:
                    if not down:
                        # oben und unten am Ende, ohne das Set: es steht gar nicht in der Liste (die Auswahl zeigt nur
                        # Sets, von denen es gerade Teile gibt; Kyotani „Power Rise“, Läufe vom 12.11.2026). Vorher lief
                        # der Bot hier weiter, meldete „0 statt 1 ausgewählt“ und blieb im offenen Fenster hängen.
                        self.close_filter_windows()
                        raise SetAbsent(T('Set {set} steht nicht in der Set-Auswahl', set=set_name))
                    down, stuck = False, 0
            else:
                stuck = 0
            prev = img
            # erst nach unten blättern bis ans Ende (die Sets stehen nach dem Alphabet; vorher reichten 5 Wischer
            # nicht bis „Status EnH.“, Test 08.11.2026), dann wieder nach oben. Auf einer Set-Karte anfangen:
            # die Mitte einer Zeile mit Set-Namen.
            names = sorted([b for b in boxes if V.set_from_text(b.text)], key=lambda b: b.cy)
            ya = names[len(names) // 2].cy + 40 if names else 560
            ya = max(380, min(620, ya))
            if down:
                self.dev.swipe(800, ya, 800, ya - 260, ms=500, wait=0.9)
            else:
                self.dev.swipe(800, ya - 260, 800, ya, ms=500, wait=0.9)
        else:
            self.shot('set_nicht_gefunden')
            self.close_filter_windows()
            raise BotError(T('Set {set} in der Auswahl nicht gefunden', set=set_name))
        img = self.snap()
        n = selected(img)
        if n != 1:
            self.shot('setauswahl_falsch')
            self.close_filter_windows()
            raise BotError(T('Set-Auswahl zeigt {n} statt 1 ausgewählt', n=n))
        oks = [b for b in self.ocr(img, (1100, 680, 1330, 770)) if b.n == 'ok']
        x, y = (oks[0].cx, oks[0].cy) if oks else L.SP_OK
        self.dev.tap(x, y, 'Set OK', wait=1.0)

    def cells(self, img):
        h = L.INV_CELL / 2
        out = []
        for y in L.INV_ROWS:
            for x in L.INV_COLS:
                if V.golden_ratio(img, (x - h, y - h, x + h, y + h)) > 0.15:
                    out.append((x, y))
        return out

    def close_inv_popup(self):
        self.dev.tap(*L.INV_CLOSE, 'Fenster schließen', wait=0.4)
        self.wait(lambda im: not self.popup_open(im, L.INV_PANELS), timeout=3)

    def find_in_list(self, slot, target: Piece):
        """Sucht das Teil in der Liste des Reiters. Lässt das Fenster des gefundenen Teils offen."""
        seen = set()
        for page in range(10):
            img = self.snap()
            cells = self.cells(img)
            new = 0
            for (x, y) in cells:
                self.dev.tap(x, y, 'Teil in der Liste ansehen', wait=0.4)
                panels = self.read_popup(L.INV_PANELS, slot)
                if not panels:
                    self.close_inv_popup()
                    continue
                cand = [p for p in panels if p.button == 'Equip']
                cand = cand[-1] if cand else None
                if not cand:
                    self.close_inv_popup()
                    continue
                sig = cand.piece.label()
                if sig in seen:
                    self.close_inv_popup()
                    continue
                seen.add(sig)
                new += 1
                if same_piece(target, cand.piece):
                    return cand
                self.close_inv_popup()
                # das Spiel sortiert nach Stufe, höchste zuerst: ab einer niedrigeren Stufe kommt das Teil nicht mehr
                if cand.piece.lv is not None and target.lv is not None and cand.piece.lv < target.lv:
                    return None
            if len(cells) < len(L.INV_COLS) * len(L.INV_ROWS) or new == 0:
                break
            # auf einer Kachel anfangen (zwischen zwei Spalten oder Reihen bewegt sich die Liste nicht) und ohne
            # Nachlaufen ziehen, damit keine Reihe übersprungen wird (Tests 07./08.11.2026)
            x = L.INV_COLS[1]
            y0 = self.tile_y(img, x, 600) or 600
            if not self.dev.sdrag(x, y0, x, y0 - 300, ms=700, hold=0.4, wait=0.8):
                self.dev.swipe(x, y0, x, y0 - 310, ms=1200, wait=1.2)
        return None

    def tile_y(self, img, x, y, span=110):
        """Eine y-Stelle bei y, die in Spalte x sicher auf einer goldenen Kachel liegt (40 von jeder Lücke), sonst None."""
        ys = list(range(int(y - span - 60), int(y + span + 60), 4))
        gap = [V.golden_ratio(img, (x - 60, yy - 3, x + 60, yy + 3)) < 0.03 for yy in ys]
        if not any(gap) or all(gap):
            return None
        gaps = [yy for yy, g in zip(ys, gap) if g]
        best = None
        for yy in ys:
            if abs(yy - y) <= span and min(abs(yy - g) for g in gaps) >= 40 and (best is None or abs(yy - y) < abs(best - y)):
                best = yy
        return best

    def equip_here(self, slot, target: Piece, status='locked'):
        """Im offenen Inventar: Teil im Reiter suchen (Filter: Set, Legendary, Status) und anziehen.
        'ok', 'dry' oder 'missing'."""
        if self.USE_MAIN_FILTER:
            self.tab(slot)
        try:
            self.set_filter(target.set, slot, target.main, status)
        except SetAbsent:
            self.log(T('    {slot}: kein Teil {set} im Inventar (nicht in der Set-Auswahl)', slot=slot, set=target.set))
            return 'missing'
        self.tab(slot)
        cand = self.find_in_list(slot, target)
        if not cand:
            return 'missing'
        if self.dry:
            self.close_inv_popup()
            return 'dry'
        if not cand.button_box or norm(cand.button_box.text) != 'equip':
            raise SafetyStop(T('Der Knopf zum Anziehen heißt nicht „Equip“. Abgebrochen.'))
        # Bei langem Set-Text reicht das Fenster so weit nach unten, dass sein roter Knopf „Equip“ dort liegt, wo auf der
        # Seite „Quick Equip/Unequip“ sitzt (Sicherheits-Stopp bei Oikawa, Lauf „Test2“ vom 11.11.2026). Der Knopf des
        # Fensters hat links neben sich „MAX“; steht das da, gilt für diesen einen Tipp nur die Sperre der Kauf-Knöpfe
        # oben. Die Wort-Prüfung bleibt: getippt wird nur, wo genau „Equip“ steht (1.15).
        bb = cand.button_box
        beside_max = any(b.n == 'max' and abs(b.cy - bb.cy) < 20 and 90 < bb.cx - b.cx < 260 for b in cand.boxes)
        old = self.dev.zones
        if beside_max:
            self.dev.zones = []
        try:
            self.dev.tap(bb.cx, bb.cy, 'Equip', wait=1.2)
        finally:
            self.dev.zones = old
        self.wait(lambda im: not self.popup_open(im, L.INV_PANELS), timeout=4)
        # nachsehen: Platz im Kreis antippen und lesen. Direkt nach "Equip" läuft im Kreis noch eine Animation; ein
        # Tippen darauf öffnet dann kein Fenster (Kuroo Platz I im Lauf „Peak“, 09.11.2026: Teil saß richtig, der Bot
        # brach trotzdem ab). Darum warten und bis zu dreimal tippen.
        time.sleep(1.0)
        panels = None
        for attempt in range(3):
            self.dev.tap(*L.INV_CIRCLE_SLOT[slot], f'Platz {slot} prüfen', wait=0.5 + 0.7 * attempt)
            panels = self.read_popup(L.INV_PANELS, slot)
            if panels:
                break
        if not panels:
            # kein Fenster: nicht abbrechen, die Prüfung am Ende liest den Spieler noch einmal
            self.shot(f'equip_{slot}_pruefung')
            self.log(T('    Hinweis Platz {slot}: Kontrolle nach „Equip“ nicht lesbar, wird am Ende geprüft', slot=slot))
            return 'ok'
        ok = any(same_piece(target, p.piece) for p in panels if p.button == 'Unequip')
        if not ok:
            # noch einmal hinsehen, bevor es als Fehler zählt: mit offenem Fenster ein Bild fürs Protokoll, dann das
            # Fenster schließen, neu öffnen und vergrößert lesen (1.15)
            self.shot(f'equip_{slot}_pruefung_fenster')
            for p in panels:
                self.log(T('    Kontrolle Platz {slot}: gelesen {label} [{btn}]', slot=slot, label=p.piece.label(), btn=p.button))
            self.close_inv_popup()
            time.sleep(0.8)
            self.dev.tap(*L.INV_CIRCLE_SLOT[slot], f'Platz {slot} prüfen', wait=1.0)
            img, _ = self.wait(lambda im: self.popup_open(im, L.INV_PANELS), timeout=4)
            panels = V.parse_panels(self.ocr(img, L.INV_PANELS, upscale=1.5), slot) or panels
            ok = any(same_piece(target, p.piece) for p in panels if p.button == 'Unequip')
        self.close_inv_popup()
        if not ok:
            self.shot(f'equip_{slot}_pruefung')
            raise BotError(T('Platz {slot}: Nach „Equip“ sitzt dort nicht das gesuchte Teil', slot=slot))
        return 'ok'

    def equip_list(self, p, slots, why=''):
        """Geht zu Spieler p, zieht die Teile der Plätze an. Gibt die Plätze zurück, deren Teil nicht im Inventar lag."""
        self.goto(p.id)
        self.open_inventory()
        missing = []
        order = sorted(slots, key=lambda s: (p.targets[s].set, SLOTS.index(s)))
        sets = []
        for s in order:
            if p.targets[s].set not in sets:
                sets.append(p.targets[s].set)
        # Je Set erst alle Plätze mit dem Filter „locked“ (fast alle Teile auf +12 sind gesperrt), dann die noch fehlenden
        # mit „unlocked“: so wird der Filter je Set höchstens zweimal eingestellt statt zweimal je fehlendem Teil (1.15).
        for set_name in sets:
            left = [s for s in order if p.targets[s].set == set_name]
            for status in ('locked', 'unlocked'):
                for s in list(left):
                    t = p.targets[s]
                    try:
                        r = self.equip_here(s, t, status)
                    except BotError as e:
                        # ein Teil klappt nicht: als offen melden und mit dem nächsten Teil weitermachen (1.12; vorher
                        # brach der Bot die Potentials aller übrigen Spieler ab, Lauf „Test1“ vom 11.11.2026). Die
                        # Prüfung am Ende liest den Spieler noch einmal.
                        self.shot(f'equip_{s}_fehler')
                        self.changed.add(p.id)
                        self.note(p.name, s, 'später', T('{set}: nicht geschafft ({err})', set=t.set, err=e))
                        left.remove(s)
                        self.to_pp()
                        self.open_inventory()
                        continue
                    if r == 'missing':
                        continue
                    left.remove(s)
                    if r == 'ok':
                        self.current[p.id][s] = t
                        self.changed.add(p.id)
                        self.note(p.name, s, 'ok', T('{set} angezogen{why}', set=t.set, why=why))
                    else:
                        self.note(p.name, s, 'dry', T('{set} liegt im Inventar und würde angezogen', set=t.set))
            missing += left
        self.to_pp()
        return missing

    # ============================================================ Gesamtablauf
    def player_failed(self, p, err):
        """Bei einem Spieler ging etwas nicht: melden, zurück auf Player Potential, weiter mit dem nächsten (1.12)."""
        self.shot('spieler_fehler')
        self.note(p.name, '–', 'später', T('abgebrochen ({err}); weiter mit dem nächsten Spieler', err=err))
        try:
            self.to_pp()
        except BotError:
            pass

    def run(self, lo: Loadout):
        t0 = time.time()
        by_id = {p.id: p for p in lo.players}
        self.scan(lo)
        for p in lo.players:
            if p.id not in self.current:
                self.note(p.name, '–', 'fehlt', T('Spieler im Spiel nicht gefunden'))
        present = sorted([p for p in lo.players if p.id in self.current], key=lambda p: self.index_of(p.id))

        def wearer(slot, piece):
            """Wer aus der Aufstellung trägt dieses Teil gerade?"""
            for pid, cur in self.current.items():
                c = cur.get(slot)
                if c and same_piece(c, piece):
                    return pid
            return None

        def keeps(pid, slot, piece):
            """Soll dieser Spieler das Teil laut Aufstellung selbst tragen?"""
            t = by_id[pid].targets.get(slot) if pid in by_id else None
            return bool(t and same_piece(t, piece))

        def conflict(p, s, w):
            self.note(p.name, s, 'fehlt', T('{set}: trägt {name}, der es laut Aufstellung selbst behalten soll '
                                            '(Teil doppelt eingeplant)', set=p.targets[s].set, name=NAMES.get(w, w)))

        waiting = {}      # Spieler-ID -> Plätze, deren Teil ein anderer aus der Aufstellung noch trägt
        unknown = []      # (Spieler-ID, Platz): Teil weder im Inventar noch bei der Aufstellung
        for p in present:
            cur = self.current[p.id]
            todo = [s for s, t in p.targets.items() if not (cur.get(s) and same_piece(cur[s], t))]
            # Teile, die ein anderer aus der Aufstellung braucht, zuerst ausziehen
            give = []
            for s in SLOTS:
                c = cur.get(s)
                if not c or (p.targets.get(s) and same_piece(c, p.targets[s])):
                    continue
                if any(o.id != p.id and o.targets.get(s) and same_piece(c, o.targets[s]) for o in present):
                    give.append(s)
            if not todo and not give:
                self.log(T('{name}: trägt schon alles richtig.', name=p.name))
                continue
            self.log(f'{p.name}:')
            try:
                self.goto(p.id)
                for s in give:
                    if self.unequip(s, cur[s]):
                        self.note(p.name, s, 'frei', T('{set} würde ausgezogen (braucht ein anderer)', set=cur[s].set) if self.dry
                                  else T('{set} ausgezogen (braucht ein anderer)', set=cur[s].set))
                        if not self.dry:
                            cur[s] = None
                            self.changed.add(p.id)
                from_inv = []
                for s in sorted(todo, key=lambda s: (p.targets[s].set, SLOTS.index(s))):
                    t = p.targets[s]
                    w = wearer(s, t)
                    if w is not None and w != p.id and keeps(w, s, t):
                        conflict(p, s, w)
                        continue
                    if w is not None and w != p.id:
                        if self.dry:
                            self.note(p.name, s, 'tausch', T('{set}: trägt gerade {name}, würde umgezogen', set=t.set, name=NAMES.get(w, w)))
                        else:
                            waiting.setdefault(p.id, []).append(s)
                            self.note(p.name, s, 'später', T('{set}: trägt gerade {name}, kommt danach', set=t.set, name=NAMES.get(w, w)))
                        continue
                    from_inv.append(s)
                if from_inv:
                    for s in self.equip_list(p, from_inv):
                        unknown.append((p.id, s))
                        self.note(p.name, s, 'später', T('{set} liegt nicht im Inventar, suche es bei anderen Spielern', set=p.targets[s].set))
            except BotError as e:
                self.player_failed(p, e)

        # zweite Runde: Teile, die erst durch andere aus der Aufstellung frei geworden sind
        if waiting:
            self.log(T('Zweite Runde: Teile, die inzwischen frei sind ...'))
            for pid in sorted(waiting, key=self.index_of):
                p = by_id[pid]
                self.log(f'{p.name}:')
                free = []
                for s in waiting[pid]:
                    w = wearer(s, p.targets[s])
                    if w is not None and w != pid:
                        conflict(p, s, w)
                    else:
                        free.append(s)
                if not free:
                    continue
                try:
                    for s in self.equip_list(p, free, T(' (2. Runde)')):
                        unknown.append((pid, s))
                        self.note(p.name, s, 'später', T('{set} ist noch nicht frei, suche es bei anderen Spielern', set=p.targets[s].set))
                except BotError as e:
                    self.player_failed(p, e)

        # dritte Runde: Teile bei Spielern außerhalb der Aufstellung suchen
        if unknown:
            self.find_elsewhere(unknown, by_id)

        t = int(time.time() - t0)
        self.log(T('Fertig nach {m} min {s} s.', m=t // 60, s=t % 60))
        return self.report

    def find_elsewhere(self, pending, by_id):
        """Sucht fehlende Teile bei Spielern außerhalb der Aufstellung, zieht sie dort aus und dann richtig an."""
        need = {}
        for pid, s in pending:
            need.setdefault(s, []).append((pid, by_id[pid].targets[s]))
        self.log(T('Suche fehlende Teile bei Spielern außerhalb der Aufstellung (das dauert etwas) ...'))
        if self.off_ring:
            self.back_on_ring()
        freed = {}
        visited = []
        count = 0

        def visit():
            nonlocal count
            k = self.ring[self.pos]
            if any(same_key(k, v) for v in visited):
                return
            visited.append(k)
            if k in by_id:
                return                   # Spieler der Aufstellung: seine Teile sind bekannt
            count += 1
            if count % 10 == 0:
                self.log(T('  ... {n} Spieler angesehen', n=count))
            img = self.stable_snap()
            pid, name, raw = self.player_here(img)
            who = name or raw
            for s in list(need):
                if not self.slot_filled(img, s):
                    continue
                try:
                    piece = self.read_slot(s)
                except BotError as e:             # ein Platz nicht lesbar: weiter, nicht alles abbrechen (1.15)
                    self.log(T('  {who}, Platz {slot}: nicht lesbar ({err}), weiter', who=who, slot=s, err=e))
                    try:
                        self.to_pp()
                    except BotError:
                        pass
                    continue
                if not piece:
                    continue
                for (tp, t) in list(need[s]):
                    if not same_piece(piece, t):
                        continue
                    self.log(T('  {name}, Platz {slot}: das Teil trägt {who}', name=by_id[tp].name, slot=s, who=who))
                    if self.dry:
                        self.note(by_id[tp].name, s, 'tausch', T('{set}: trägt {who}, würde umgezogen', set=t.set, who=who))
                    elif self.unequip(s, piece):
                        self.note(who, s, 'frei', T('{set} ausgezogen für {name}', set=t.set, name=by_id[tp].name))
                        freed.setdefault(tp, []).append(s)
                    need[s].remove((tp, t))
                    break
                if not need[s]:
                    del need[s]

        def all_seen():
            return all(any(same_key(k, v) for v in visited) for k in self.ring)

        # Läuft die Liste im Kreis, geht es so lange vorwärts, bis jeder einmal dran war (auch wenn sich die
        # Reihenfolge durch das Ausziehen verschiebt). Hat sie Enden: erst bis zum einen, dann bis zum anderen.
        visit()
        for d in (1, -1):
            steps = 0
            while need and steps < 3 * self.max_players:
                r = self.move(d)
                steps += 1
                if r is None:
                    break
                visit()
                if self.closed and all_seen():
                    break
            if not need or self.closed:
                break

        for pid in sorted(freed, key=self.index_of):
            p = by_id[pid]
            self.log(f'{p.name}:')
            for s in self.equip_list(p, freed[pid], T(' (war bei einem anderen Spieler)')):
                self.note(p.name, s, 'fehlt', T('{set} nach dem Ausziehen nicht im Inventar gefunden', set=p.targets[s].set))
        for s, lst in need.items():
            for pid, t in lst:
                self.note(by_id[pid].name, s, 'fehlt', T('{set} nirgends gefunden: {label}', set=t.set, label=t.label()))

    def verify(self, lo: Loadout):
        """Liest am Ende die Spieler der Aufstellung noch einmal, bei denen sich etwas geändert hat."""
        bad = []
        todo = [p for p in lo.players if p.id in self.changed and self.index_of(p.id) is not None]
        for p in sorted(todo, key=lambda p: self.index_of(p.id)):
            try:
                self.goto(p.id)
            except BotError as e:
                # nicht den ganzen Lauf abbrechen: die Memories kommen danach noch (1.15)
                bad.append((p.name, '–', T('nicht geprüft ({err})', err=e)))
                continue
            cur = self.read_all()
            for s, t in p.targets.items():
                if not (cur.get(s) and same_piece(cur[s], t)):
                    # einmal neu lesen: ab und zu fehlt der Texterkennung eine Zeile (Iwaizumi, Platz IV, 11.11.2026)
                    try:
                        time.sleep(0.5)
                        cur[s] = self.read_slot(s) if self.slot_filled(self.stable_snap(), s) else None
                    except BotError:
                        pass
                if not (cur.get(s) and same_piece(cur[s], t)):
                    bad.append((p.name, s, piece_diff(t, cur[s]) if cur.get(s) else T('leer')))
        return bad
