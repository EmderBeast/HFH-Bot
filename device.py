"""Verbindung zum LDPlayer über ADB: Bildschirmfoto und Tippen.

ADB kommt mit dem LDPlayer mit (adb.exe im Installationsordner). Im LDPlayer muss unter
Einstellungen -> Andere -> "ADB-Debugging" auf "Lokale Verbindung öffnen" stehen.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

import struct

import cv2
import numpy as np

import fastadb

import layout as L
import texts
from texts import T
from vision import REF_H, REF_W, norm, set_from_text, set_from_title

ADB_CANDIDATES = [
    r'C:\LDPlayer\LDPlayer14\adb.exe',
    r'C:\LDPlayer\LDPlayer9\adb.exe',
    r'C:\LDPlayer\LDPlayer4.0\adb.exe',
    r'D:\LDPlayer\LDPlayer14\adb.exe',
    r'D:\LDPlayer\LDPlayer9\adb.exe',
]
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0   # kein schwarzes Fenster bei jedem Aufruf


class DeviceError(RuntimeError):
    pass


class SafetyStop(RuntimeError):
    """Das Programm wollte etwas antippen, das es nie antippen darf."""


def danger_text(text: str) -> bool:
    """Steht hier ein Knopf, den das Programm nie drückt (MAX, Enhance, Dismantle, Quick Equip, Kaufen, Sperren ...)?"""
    if set_from_text(text) or set_from_title(text):
        return False
    n = norm(text)
    if any(p in n for p in L.DANGER_PARTS):
        return True
    words = re.findall(r'[a-z]+', text.lower())
    return any(w.startswith(d) for w in words for d in L.DANGER_WORDS)


def find_adb(explicit: str | None = None) -> str:
    if explicit and os.path.exists(explicit):
        return explicit
    for p in ADB_CANDIDATES:
        if os.path.exists(p):
            return p
    p = shutil.which('adb')
    if p:
        return p
    raise DeviceError(T('adb.exe nicht gefunden. Trag den Pfad in potbot.ini ein (z. B. C:\\LDPlayer\\LDPlayer14\\adb.exe).'))


class Device:
    def __init__(self, adb: str, serial: str | None = None, log=print):
        self.adb = adb
        self.serial = serial
        self.log = log
        self.size = None            # (Breite, Höhe) des Emulators
        self.last = None            # letztes Bildschirmfoto
        self.stale = True           # seit dem letzten Bildschirmfoto wurde getippt oder gewischt
        self.reader = None          # wird vom Bot gesetzt (für die Sicherheitsprüfung)
        self.zones = None           # gesperrte Bereiche der offenen Seite (Account-Scan); None: die des Pot-Bots (L.DANGER)
        self.tempo = 0.7            # Faktor für die Pausen nach Tippen und Wischen (potbot.ini: tempo)
        self.fast = True            # Bildschirmfoto roh über den ADB-Server (1.15); False: wie früher als PNG über adb.exe
        self.fast_fail = 0

    # ------------------------------------------------------------ ADB
    def _run(self, args, timeout=20, binary=False):
        cmd = [self.adb] + (['-s', self.serial] if self.serial else []) + args
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise DeviceError(T('ADB antwortet nicht: {cmd}', cmd=' '.join(args)))
        if r.returncode != 0:
            raise DeviceError((r.stderr or r.stdout).decode('utf-8', 'replace').strip() or T('ADB-Fehler'))
        return r.stdout if binary else r.stdout.decode('utf-8', 'replace')

    def connect(self) -> str:
        """Sucht den Emulator. Gibt die Seriennummer zurück."""
        self._run(['start-server'], timeout=30)
        devs = self._devices()
        if not devs:
            # LDPlayer meldet sich manchmal erst nach "adb connect"
            for port in (5555, 5557, 5559):
                try:
                    self._run(['connect', f'127.0.0.1:{port}'], timeout=10)
                except DeviceError:
                    pass
            devs = self._devices()
        if not devs:
            raise DeviceError(T('Kein Emulator gefunden. Läuft der LDPlayer, und ist ADB-Debugging eingeschaltet?'))
        if self.serial and self.serial not in devs:
            raise DeviceError(T('Gerät {serial} nicht gefunden. Gefunden: {found}', serial=self.serial, found=', '.join(devs)))
        if not self.serial:
            # bevorzugt emulator-5554 (erste LDPlayer-Instanz); doppelte Einträge derselben Instanz sind egal
            self.serial = 'emulator-5554' if 'emulator-5554' in devs else devs[0]
        img = self.screenshot()
        h, w = img.shape[:2]
        self.size = (w, h)
        ratio = w / h
        if abs(ratio - 16 / 9) > 0.02:
            raise DeviceError(T('Der Emulator hat {w} x {h}. Das Programm braucht 16:9 (z. B. 1600 x 900 oder 1920 x 1080). '
                                'Stell im LDPlayer unter Einstellungen -> Erweitert eine 16:9-Auflösung ein.', w=w, h=h))
        return self.serial

    def _devices(self):
        out = self._run(['devices'], timeout=15)
        devs = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == 'device':
                devs.append(parts[0])
        return devs

    def _raw_shot(self):
        """Bildschirmfoto roh (ohne PNG) direkt vom ADB-Server: 0,4 s statt 0,85 s (Messung 11.11.2026). None, wenn
        die Antwort nicht passt."""
        raw = fastadb.run(self.serial, 'exec:screencap', 20)
        if len(raw) < 16:
            return None
        w, h = struct.unpack('<II', raw[:8])
        head = len(raw) - w * h * 4
        if not (0 < w <= 8000 and 0 < h <= 8000) or head not in (8, 12, 16):
            return None
        arr = np.frombuffer(raw, np.uint8, offset=head).reshape(h, w, 4)
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

    def screenshot(self) -> np.ndarray:
        if self.fast and self.serial:
            try:
                img = self._raw_shot()
            except Exception:
                img = None
            if img is not None:
                self.fast_fail = 0
                self.last = img
                self.stale = False
                return img
            self.fast_fail += 1
            if self.fast_fail >= 3:
                self.fast = False
                self.log(T('  (schnelles Bildschirmfoto geht nicht, nehme das langsame)'))
        for attempt in range(3):
            raw = self._run(['exec-out', 'screencap', '-p'], timeout=20, binary=True)
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                self.last = img
                self.stale = False
                return img
            time.sleep(0.5)
        raise DeviceError(T('Bildschirmfoto vom Emulator fehlgeschlagen'))

    # ------------------------------------------------------------ Tippen (Referenz-Koordinaten)
    def _px(self, x, y):
        w, h = self.size
        return int(round(x * w / REF_W)), int(round(y * h / REF_H))

    def _check(self, x, y, purpose):
        zones = L.DANGER if self.zones is None else list(self.zones) + L.DANGER_TOP
        for (x0, y0, x1, y1) in zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                raise SafetyStop(T('Abgebrochen: „{purpose}“ läge auf einem gesperrten Knopf ({x}, {y}).',
                                   purpose=texts.purpose(purpose), x=round(x), y=round(y)))
        if self.reader is None:
            return
        # geprüft wird immer das aktuelle Bild: nach dem letzten Tippen kann ein neues Fenster offen sein
        if self.stale or self.last is None:
            self.screenshot()
        boxes = self.reader.read(self.last, (x - 130, y - 35, x + 130, y + 35))
        for b in boxes:
            if b.x0 - 6 <= x <= b.x1 + 6 and b.y0 - 6 <= y <= b.y1 + 6 and danger_text(b.text):
                raise SafetyStop(T('Abgebrochen: An der Stelle für „{purpose}“ steht „{text}“.',
                                   purpose=texts.purpose(purpose), text=b.text))

    def tap(self, x, y, purpose: str, wait: float = 0.8, check: bool = True):
        if check:
            self._check(x, y, purpose)
        px, py = self._px(x, y)
        self.stale = True
        self._run(['shell', 'input', 'tap', str(px), str(py)])
        time.sleep(max(0.2, wait * self.tempo))

    def swipe(self, x0, y0, x1, y1, ms: int = 450, wait: float = 0.9):
        a = self._px(x0, y0)
        b = self._px(x1, y1)
        self.stale = True
        self._run(['shell', 'input', 'swipe', str(a[0]), str(a[1]), str(b[0]), str(b[1]), str(ms)])
        time.sleep(max(0.3, wait * self.tempo))

    def touch_info(self):
        """Sucht das Touch-Gerät des Emulators (für sendevent). Gibt dict oder None."""
        if hasattr(self, '_touch'):
            return self._touch
        self._touch = None
        try:
            out = self._run(['shell', 'getevent -pl'], timeout=15)
        except DeviceError:
            return None
        cur = None
        for line in out.splitlines():
            m = re.match(r'add device \d+: (\S+)', line.strip())
            if m:
                cur = {'dev': m.group(1), 'slot': False, 'btn': False, 'press': False, 'major': False}
                continue
            if cur is None:
                continue
            t = line.strip()
            for key, name in (('slot', 'ABS_MT_SLOT'), ('btn', 'BTN_TOUCH'), ('press', 'ABS_MT_PRESSURE'),
                              ('major', 'ABS_MT_TOUCH_MAJOR')):
                if name in t:
                    cur[key] = True
            for axis in ('X', 'Y'):
                m = re.search(r'ABS_MT_POSITION_' + axis + r'\b.*?max (\d+)', t)
                if m:
                    cur['max' + axis] = int(m.group(1))
            if 'maxX' in cur and 'maxY' in cur and self._touch is None:
                self._touch = cur
        return self._touch

    def sdrag(self, x0, y0, x1, y1, ms: int = 600, hold: float = 0.4, steps: int = 12, wait: float = 0.5):
        """Ziehen über sendevent: gleichmäßig schnell, am Ende still halten (kein Nachlaufen der Liste).
        Gibt False zurück, wenn es kein passendes Touch-Gerät gibt."""
        t = self.touch_info()
        if not t:
            return False
        w, h = self.size
        def dev_xy(x, y):
            px, py = self._px(x, y)
            return int(px * t['maxX'] / w), int(py * t['maxY'] / h)
        D = t['dev']
        ev = []
        def e(tp, code, val):
            ev.append(f'sendevent {D} {tp} {code} {val}')
        def pos(x, y):
            dx, dy = dev_xy(x, y)
            e(3, 53, dx)
            e(3, 54, dy)
        if t['slot']:
            e(3, 47, 0)
        e(3, 57, 7)
        pos(x0, y0)
        if t['press']:
            e(3, 58, 60)
        if t['major']:
            e(3, 48, 6)
        if t['btn']:
            e(1, 330, 1)
        if not t['slot']:
            e(0, 2, 0)
        e(0, 0, 0)
        pause = max(0.0, ms / 1000 / steps)
        for i in range(1, steps + 1):
            ev.append(f'sleep {pause:.3f}')
            pos(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
            if not t['slot']:
                e(0, 2, 0)
            e(0, 0, 0)
        ev.append(f'sleep {hold}')
        if t['slot']:
            e(3, 57, -1)
        if t['btn']:
            e(1, 330, 0)
        if not t['slot']:
            e(0, 2, 0)
        e(0, 0, 0)
        self.stale = True
        try:
            self._run(['shell', '; '.join(ev)], timeout=30)
        except DeviceError:
            return False
        time.sleep(wait)
        return True

    def drag(self, x0, y0, x1, y1, steps: int = 6, hold: float = 0.4, wait: float = 0.6):
        """Ziehen wie mit der Maus: Finger runter, in Schritten bewegen, kurz still halten, loslassen.
        Das Halten am Ende nimmt den Schwung raus. Hilft, wenn eine Liste auf „input swipe“ nicht mehr reagiert
        (Potential-Inventar, Test 07.11.2026: Maus-Ziehen bewegte die Liste, der adb-Wischer nicht).
        Gibt False zurück, wenn der Emulator „input motionevent“ nicht kennt."""
        if getattr(self, 'no_motion', False):
            return False
        a = self._px(x0, y0)
        b = self._px(x1, y1)
        parts = [f'input motionevent DOWN {a[0]} {a[1]}']
        for i in range(1, steps + 1):
            x = round(a[0] + (b[0] - a[0]) * i / steps)
            y = round(a[1] + (b[1] - a[1]) * i / steps)
            parts.append(f'input motionevent MOVE {x} {y}')
        parts += [f'sleep {hold}', f'input motionevent UP {b[0]} {b[1]}']
        self.stale = True
        try:
            out = self._run(['shell', '; '.join(parts)], timeout=30)
        except DeviceError:
            out = 'Error'
        if 'rror' in out or 'nknown' in out or 'sage' in out:
            self.no_motion = True
            return False
        time.sleep(wait)
        return True
