"""HFH-Bot: zieht im LDPlayer die Potentials an, die im CC-Simulator auf den Spielern liegen, und liest den Account
für den Simulator aus (Account-Scan).

Start: start.bat (Doppelklick). Abbrechen jederzeit mit Strg+C. Sprache: Deutsch oder Englisch (potbot.ini).
"""
from __future__ import annotations

import configparser
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import plan as P  # noqa: E402
import texts  # noqa: E402
from texts import T, yes  # noqa: E402

INI = os.path.join(HERE, 'potbot.ini')
LOADOUTS = os.path.join(HERE, 'loadouts')
LOGS = os.path.join(HERE, 'logs')
SCANS = os.path.join(HERE, 'scans')
OLD_FOLDERS = {'aufstellungen': LOADOUTS, 'protokolle': LOGS}     # Namen der ersten Version (1.0)
VERSION = '1.17 (15.11.2026)'


class Log:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, 'a', encoding='utf-8')

    def __call__(self, msg=''):
        print(msg, flush=True)
        self.f.write(time.strftime('%H:%M:%S ') + str(msg) + '\n')
        self.f.flush()


def config():
    c = configparser.ConfigParser()
    c.read(INI, encoding='utf-8')
    s = c['potbot'] if 'potbot' in c else {}
    return {'adb': s.get('adb') or None, 'serial': s.get('geraet') or s.get('device') or None,
            'max': int(s.get('max_spieler') or s.get('max_players') or 1000),
            'lang': (s.get('language') or '').strip().lower(), 'tempo': s.get('tempo') or '',
            'min_level': max(2, min(70, int(s.get('min_level') or 2))) if str(s.get('min_level') or '2').strip().isdigit() else 2}


def save_lang(lang):
    """Schreibt 'language = de/en' in potbot.ini (die Kommentare bleiben stehen)."""
    try:
        with open(INI, encoding='utf-8') as f:
            text = f.read()
    except OSError:
        text = '[potbot]\n'
    line = f'language = {lang}'
    if re.search(r'(?m)^[ \t]*language[ \t]*=.*$', text):
        text = re.sub(r'(?m)^[ \t]*language[ \t]*=.*$', line, text, count=1)
    else:
        text = text.rstrip('\n') + '\n' + line + '\n'
    with open(INI, 'w', encoding='utf-8') as f:
        f.write(text)


def choose_lang():
    lang = config()['lang']
    if lang not in ('de', 'en'):
        a = ask('Sprache / Language:  d = Deutsch,  e = English  > ')
        lang = 'en' if a.lower().startswith('e') else 'de'
        save_lang(lang)
    texts.set_lang(lang)


def migrate_folders():
    """Ordner der ersten Version (aufstellungen, protokolle) übernehmen."""
    for old, new in OLD_FOLDERS.items():
        old = os.path.join(HERE, old)
        if not os.path.isdir(old):
            continue
        if not os.path.exists(new):
            os.rename(old, new)
            continue
        for name in os.listdir(old):
            src, dst = os.path.join(old, name), os.path.join(new, name)
            if not os.path.exists(dst):
                shutil.move(src, dst)


def clipboard() -> str:
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        t = r.clipboard_get()
        r.destroy()
        if t:
            return t
    except Exception:
        pass
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              '[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw'],
                             capture_output=True, timeout=15)
        return out.stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


def loadouts():
    out = []
    for path in sorted(glob.glob(os.path.join(LOADOUTS, '*.json'))):
        try:
            with open(path, encoding='utf-8') as f:
                text = f.read()
            name = os.path.splitext(os.path.basename(path))[0]
            out.append((name, path, P.parse(text, name)))
        except Exception as e:  # kaputte Datei: anzeigen, nicht abbrechen
            out.append((os.path.basename(path), path, e))
    return out


def ask(q, default=''):
    try:
        a = input(q).strip()
    except EOFError:
        a = ''
    return a or default


def show_loadout(lo):
    print(f'  {lo.summary()}')
    for p in lo.players:
        sets = {}
        for t in p.targets.values():
            sets[t.set] = sets.get(t.set, 0) + 1
        sets_txt = ' + '.join(f'{n}× {s}' for s, n in sorted(sets.items(), key=lambda x: -x[1]))
        mem = ''
        if p.memory:
            from plan import GAME
            m = GAME['memories'].get(str(p.memory[0])) or {}
            mem = T('  Memory: {name} ({tier})', name=m.get('name', p.memory[0]), tier=['I', 'II', 'III', 'IV', 'V'][p.memory[1] - 1])
        print('    ' + T('{name} {n} Teile  {sets}', name=f'{p.name:42s}', n=len(p.targets), sets=sets_txt) + mem)
    for n, why in lo.skipped:
        print(T('    (übersprungen) {name}: {why}', name=n, why=why))
    for w in lo.warnings:
        print(T('    Achtung: {text}', text=w))


def new_loadout():
    print()
    print(T('So kommt eine Aufstellung aus dem CC-Simulator hierher:'))
    for line in (T('  1. Potential-Rechner: Ziel „Für den Score“ oder „Fürs Rating“, „Berechnen“, dann „Alle anziehen“.'),
                 T('     (Simulator auf Englisch: Potential calculator → For the score / For the rating → Calculate → Equip all)'),
                 T('  2. Menü „Speichern“ → Kasten „Export / Import“ → „Kopieren“ (auf Englisch: Save → Export / import → Copy).'),
                 T('  3. Hier nur Enter drücken. Einfügen musst du nichts: Der Bot liest den Text selbst aus der Zwischenablage.')):
        if line.strip():
            print(line)
    texts_in, lo = [], None
    while True:
        ask(T('Wenn der Text kopiert ist, Enter drücken ...'))
        text = clipboard().strip()
        problem = None
        if not text:
            problem = T('Die Zwischenablage ist leer.')
        elif text in texts_in:
            problem = T('Das ist derselbe Text wie eben. Erst im Simulator das nächste Team öffnen und kopieren.')
        else:
            try:
                P.parse(text)
            except P.PlanError as e:
                problem = T('Das ging nicht: {err}', err=e)
        if problem:
            print(problem)
            if yes(ask(T('Noch einmal versuchen? (j/n) '))):
                continue
            break
        texts_in.append(text)
        lo = P.parse_many(texts_in)
        show_loadout(lo)
        if not yes(ask(T('Noch ein Team dazunehmen (z. B. Team 2 und 3 fürs Rating)? (j/n) '))):
            break
    if lo is None:
        print(T('Nichts gespeichert.'))
        return
    name = ask(T('Name für diese Aufstellung (z. B. Rating oder Score): '))
    if not name:
        print(T('Nicht gespeichert.'))
        return
    existing = {item[0].lower() for item in loadouts()}
    if name.lower() in existing and not yes(ask(T('„{name}“ gibt es schon. Ersetzen? (j/n) ', name=name))):
        print(T('Nicht gespeichert.'))
        return
    path = P.save(lo, LOADOUTS, name)
    print(T('Gespeichert: {path}', path=path))


def delete_loadout(items):
    n = ask(T('Welche Nummer löschen? '))
    if not n.isdigit() or not (1 <= int(n) <= len(items)):
        return
    name, path = items[int(n) - 1][:2]
    if yes(ask(T('„{name}“ wirklich löschen? (j/n) ', name=name))):
        os.remove(path)
        print(T('Gelöscht.'))


def connect(log):
    from device import Device, find_adb
    from vision import Reader
    cfg = config()
    dev = Device(find_adb(cfg['adb']), cfg['serial'], log)
    try:
        dev.tempo = max(0.4, min(1.5, float(str(cfg['tempo']).replace(',', '.')))) if cfg['tempo'] else dev.tempo
    except ValueError:
        pass
    serial = dev.connect()
    log(T('Verbunden mit {serial} ({w} x {h}).', serial=serial, w=dev.size[0], h=dev.size[1]))
    log(T('Lade die Texterkennung ...'))
    return dev, Reader(), cfg


def test_connection():
    log = Log(os.path.join(LOGS, time.strftime('%Y-%m-%d_%H%M%S') + '_test.txt'))
    from scan import Scanner
    dev, reader, cfg = connect(log)
    bot = Scanner(dev, reader, log, debug_dir=os.path.join(LOGS, 'screenshots'))
    img = bot.snap()
    path = bot.shot('test')
    kind = bot.page(img)
    pages = {'pp': 'Player Potential', 'inv': 'Potential Inventory', 'manage': 'Manage Players', 'stats': 'Player Stats',
             'memory': 'Featured Memory', 'manage_inv': 'Manage Inventory', 'gallery': 'Gallery',
             'bond_gallery': 'Bond Gallery', 'bond_info': 'Bond Info', 'team': 'Team Lineup', 'cheer': 'Team Cheer',
             'coach': 'Coach',
             'home': T('Startbildschirm'), 'other': T('unbekannt')}
    log(T('Seite im Spiel: {page}', page=pages.get(kind, kind)))
    if kind == 'pp':
        pid, name, raw = bot.player_here(img)
        log(T('Spieler: {name}', name=name if pid else T('{raw} (nicht erkannt)', raw=repr(raw))))
        log(T('Lese seine Teile (öffnet nur die Fenster der Teile) ...'))
        for slot, piece in bot.read_all().items():
            line = f'  {slot:3s} ' + (piece.label() if piece else T('leer'))
            if piece and piece.problems:
                line += T('  [unsicher: {problems}]', problems='; '.join(piece.problems))
            log(line)
    else:
        import scan as S
        import layout as L
        if kind == 'stats':
            pid, name, raw = bot.player_here(img)
            log(T('Spieler: {name}', name=name if pid else T('{raw} (nicht erkannt)', raw=repr(raw))))
            log(f'  Rating {bot.read_number(img, L.PS_RATING)} · Lv {bot.read_level(img)} · Skills {bot.read_skills(img)}'
                f' · {T("Summe")} {bot.read_total(img)}')
        elif kind == 'memory':
            m = bot.read_memory_panel(img)
            mid = S.match_memory_effect(m['frags'], m['eff'], m['tier'])
            log(f'  Memory: {S.MEMS[mid]["name"] if mid else "?"} · Tier {m["tier"]}')
        elif kind == 'manage_inv':
            name = ' '.join(b.text for b in bot.ocr(img, L.MI_NAME))
            mid = S.match_memory_name(name)
            log(f'  Memory: {S.MEMS[mid]["name"] if mid else repr(name)} · Tier {S.tier_of([b.text for b in bot.ocr(img, L.MI_TIER)])}')
        elif kind == 'bond_info':
            info = bot.read_bond_info(img)
            if info:
                log(f'  Bond: {info[0]} · Lv {info[1]} · {S.match_bond(info[0], info[2], info[3])}')
        elif kind == 'coach':
            cid, name = bot.read_coach_header(img)
            log(f'  Coach: {S.COACHES[cid]["name"] if cid else repr(name)}')
            for p in bot.read_pas(img):
                log(f'    {p["pos"]} {p["t"]} {p["v"]}  | {p["text"][:60]}')
        elif kind == 'cheer' and bot.cheer_popup(img):
            name, a, d = bot.read_cheer_popup(img)
            cid = S.match_cheer(name)
            log(f'  Cheer: {cid} · {a} · {d} · {S.cheer_stars(cid, a, d) if cid and a else "?"}★')
        log(T('Für den Test des Pot-Bots im Spiel die Seite „Player Potential“ öffnen.'))
    log(T('Bildschirmfoto gespeichert: {path}', path=path))


def run_loadout(lo, dry):
    stamp = time.strftime('%Y-%m-%d_%H%M%S')
    log = Log(os.path.join(LOGS, f'{stamp}_{lo.name}{T("_probelauf") if dry else ""}.txt'))
    from bot import Bot, BotError
    from device import DeviceError, SafetyStop
    log(T('HFH Potential-Bot {version} · Aufstellung „{name}“ · {mode}', version=VERSION, name=lo.name,
          mode=T('PROBELAUF (nichts wird geändert)') if dry else T('ANZIEHEN')))
    try:
        dev, reader, cfg = connect(log)
        bot = Bot(dev, reader, log, dry=dry, max_players=cfg['max'], debug_dir=os.path.join(LOGS, 'screenshots', stamp))
        # auch Spieler, die nur eine Memory bekommen: der Bot muss sie finden, um bei ihnen die Memory zu wechseln (1.15)
        pots = P.Loadout(lo.name, [p for p in lo.players if p.targets or p.memory], lo.skipped, lo.warnings)
        stopped = None
        nav = None
        if pots.players:
            # Die Pfeile auf „Player Potential“ gehen nur durch die Liste, aus der die Seite geöffnet wurde. Über „Manage
            # Players“ sind es alle Spieler (so findet sie auch der Account-Scan); aus der Aufstellung nur deren Spieler
            # (Lauf „Test1“, 11.11.2026: 17 Spieler, Sakunami nicht gefunden). Darum öffnet der Bot die Seite selbst (1.12).
            try:
                import layout as L
                from scan import Scanner
                log(T('Öffne „Player Potential“ über „Manage Players“, damit die Pfeile alle Spieler zeigen ...'))
                nav = Scanner(dev, reader, log, debug_dir=os.path.join(LOGS, 'screenshots', stamp))
                nav.sort_level = True               # alle hochgelevelten Spieler vorn (1.15), am Ende wieder Deploy
                bot.nav = nav                       # für Spieler, die die Pfeile nicht erreichen (1.14)
                nav.players_page('pp', L.MP_POTENTIAL, 'Potential')
                bot.from_first = True
            except BotError as e:
                log(T('  Das ging nicht ({err}); der Bot nimmt die Seite, die gerade offen ist.', err=e))
        try:
            report = bot.run(pots) if pots.players else []
        except BotError as e:
            # die Memories trotzdem noch machen
            stopped = e
            log(T('Potentials gestoppt: {err}', err=e))
            report = list(getattr(bot, 'report', []) or [])
            try:
                bot.to_pp()                     # vom Inventar zurück, damit die Prüfung unten lesen kann
            except Exception:
                pass
        log()
        log(T('Ergebnis:'))
        final = {}                      # pro Spieler und Platz zählt der letzte Stand
        for player, slot, status, text in report:
            final[(player, slot)] = (status, text)
        counts = {}
        for status, text in final.values():
            counts[status] = counts.get(status, 0) + 1
        names = {'ok': T('angezogen'), 'dry': T('würde aus dem Inventar angezogen'),
                 'tausch': T('würde einem anderen Spieler abgenommen'), 'frei': T('ausgezogen (für einen anderen)'),
                 'fehlt': T('nicht gefunden'), 'später': T('offen')}
        if counts:
            log('  ' + ', '.join(f'{names.get(k, k)}: {v}' for k, v in counts.items()))
        if stopped:
            log(T('  Potentials NICHT fertig: vorzeitig gestoppt ({err}). Die übrigen Teile sind noch nicht geprüft; '
                  'nach dem Lauf noch einmal starten.', err=stopped))
        elif not counts and pots.players:
            log(T('  Nichts zu tun: alle tragen schon die richtigen Teile.'))
        known = set()                   # schon als fehlend gemeldet
        for (player, slot), (status, text) in final.items():
            if status in ('fehlt', 'später'):
                known.add((player, slot))
                log(T('  FEHLT {player} Platz {slot}: {text}', player=player, slot=slot, text=text))
        if not dry and bot.changed:
            log(T('Prüfe die geänderten Spieler noch einmal ...'))
            all_bad = bot.verify(lo)
            bad_keys = {(x[0], x[1]) for x in all_bad}
            checked = {p.name for p in lo.players if p.id in bot.changed}
            # Teile, die als offen gemeldet waren, bei der Prüfung aber richtig sitzen (die Kontrolle direkt nach „Equip“
            # hatte sich verlesen: Lauf „Test2“ vom 11.11.2026, vier Teile saßen richtig)
            for (player, slot), (status, text) in final.items():
                if status == 'später' and player in checked and slot in ('I', 'II', 'III', 'IV', 'V', 'VI') and (player, slot) not in bad_keys:
                    log(T('  {player} Platz {slot}: sitzt doch richtig', player=player, slot=slot))
            bad = [x for x in all_bad if (x[0], x[1]) not in known]
            for name, slot, why in bad:
                log(T('  Abweichung {name} Platz {slot}: {why}', name=name, slot=slot, why=why))
            if not bad:
                log(T('  Alles andere sitzt wie im Simulator.') if known else T('  Alles sitzt wie im Simulator.'))
        wants = [(p.id, p.memory[0], p.memory[1]) for p in lo.players if p.memory]
        if wants:
            log()
            from scan import Scanner
            sc = nav or Scanner(dev, reader, log, debug_dir=os.path.join(LOGS, 'screenshots', stamp))
            sc.debug_walk = True
            try:
                bot.to_pp()
            except Exception:
                pass
            mrep = sc.equip_memories(wants, dry=dry, bot=bot)
            names = {'ok': T('angezogen'), 'schon': T('trug sie schon'), 'dry': T('würde wechseln'), 'fehlt': T('nicht geschafft')}
            counts = {}
            for _, st, _ in mrep:
                counts[st] = counts.get(st, 0) + 1
            log(T('Memories: {text}', text=', '.join(f'{names.get(k, k)}: {v}' for k, v in counts.items()) or '-'))
            for name, st, text in mrep:
                if st == 'fehlt':
                    log(T('  FEHLT {player}: {text}', player=name, text=text))
    except KeyboardInterrupt:
        log(T('Abgebrochen (Strg+C).'))
    except SafetyStop as e:
        log(T('SICHERHEITS-STOPP: {err}', err=e))
        try:
            bot.shot('sicherheits_stopp')
        except Exception:
            pass
    except (BotError, DeviceError, P.PlanError) as e:
        log(T('Gestoppt: {err}', err=e))
    except Exception:
        log(T('Unerwarteter Fehler:'))
        log(traceback.format_exc())
    try:
        if nav is not None:
            nav.restore_sort()
    except Exception:
        pass
    log(T('Protokoll: {path}', path=log.f.name))


def scan_parts():
    return [('players', T('Spieler: Stufe, Skills, Rating, Memory und Potentials'), T('15 bis 30 min')),
            ('pots', T('Potentials im Inventar (nur Legendary)'), T('etwa 2 s pro Teil')),
            ('memories', T('Memories im Inventar'), T('etwa 1 s pro Memory')),
            ('bonds', T('Bonds (Sammlungs-Bonds und Bond-Stufen)'), T('3 bis 8 min')),
            ('cheers', T('Cheers und Rating Bonus'), T('1 bis 2 min')),
            ('coaches', T('Coaches: Stufe, Rating und Positional Advantages'), T('2 bis 4 min'))]


def scan_menu():
    print()
    print(T('Account-Scan: liest deinen Account für den CC-Simulator aus.'))
    print(T('Er öffnet nur Seiten, Reiter und Fenster und blättert in Listen. Er kauft, verbessert, verkauft und ändert nichts.'))
    print()
    sp = scan_parts()
    for i, (key, name, dur) in enumerate(sp, 1):
        print(f'  {i}  {name}  ({dur})')
    a = ask(T('Welche Teile? Enter = alle, sonst die Nummern (z. B. 1 4 5): '))
    if a:
        nums = sorted({int(x) for x in re.findall(r'\d', a) if 1 <= int(x) <= len(sp)})
        parts = [sp[n - 1][0] for n in nums]
    else:
        parts = [p[0] for p in sp]
    if not parts:
        print(T('Keine gültige Auswahl.'))
        return
    rar = None
    lv1 = False
    if 'players' in parts:
        dflt = config()['min_level']
        a = ask(T('Spieler ab welcher Stufe lesen? Enter = {d}, höchstens 70, 0 = alle (dann mit Auswahl der Seltenheit): ', d=dflt))
        lv1 = dflt if not a.strip() else (max(2, min(70, int(a))) if a.strip().isdigit() and int(a) > 0 else False)
    if not lv1 and ('players' in parts or 'memories' in parts):
        if yes(ask(T('Nur SP-, UR- und SSR-Spieler und -Memories lesen? Viel schneller; SR, R und N stellst du im Simulator selbst ein. (j/n) '))):
            rar = {'SP', 'UR', 'SSR'}
    print()
    print(T('Vorbereitung im Spiel:'))
    print(T('  - den Startbildschirm öffnen (dort fängt der Scan an)'))
    if 'players' in parts:
        print(T('  - in „Manage Players“ keinen Filter eingeschaltet lassen (sonst fehlen Spieler)'))
    print(T('Während des Scans bitte nichts im Emulator anklicken. Abbrechen mit Strg+C; was bis dahin gelesen ist, bleibt gespeichert.'))
    if yes(ask(T('Los? (j/n) '))):
        run_scan(parts, rar, None, lv1)


def scan_summary(d):
    out = []
    if 'players' in d:
        pl = d['players']
        out.append(T('{n} Spieler ({m} mit Memory, {p} mit Potentials)', n=len(pl),
                     m=sum(1 for p in pl if p.get('memory')), p=sum(1 for p in pl if p.get('pots'))))
    if 'pots' in d:
        out.append(T('{n} Potentials im Inventar', n=len(d['pots'])))
    if 'memories' in d:
        out.append(T('{n} verschiedene Memories', n=len(d['memories'])))
    if 'collections' in d:
        out.append(T('{n} Sammlungs-Bonds, {b} Team-Bonds', n=len(d['collections']), b=len(d.get('bonds') or {})))
    if 'cheers' in d:
        out.append(T('{n} Cheers', n=len(d['cheers'].get('list') or [])))
    if 'coaches' in d:
        out.append(T('{n} Coaches', n=len(d['coaches'])))
    return out


def run_scan(parts, rarities=None, base=None, lv1=False):
    stamp = time.strftime('%Y-%m-%d_%H%M')
    log = Log(os.path.join(LOGS, f'{stamp}_scan.txt'))
    os.makedirs(SCANS, exist_ok=True)
    out = os.path.join(SCANS, f'hfh-scan-{stamp}.json')
    from bot import BotError
    from device import DeviceError, SafetyStop
    from scan import Scanner
    log(T('HFH-Bot {version} · Account-Scan ({parts})', version=VERSION, parts=', '.join(parts)))

    def save(data):
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out)

    sc = None
    try:
        dev, reader, cfg = connect(log)
        sc = Scanner(dev, reader, log, max_players=cfg['max'], debug_dir=os.path.join(LOGS, 'screenshots', stamp), save=save)
        sc.base = base
        sc.lv1_stop = bool(lv1)
        sc.sort_level = bool(lv1)               # nach Level sortiert: die Mindeststufe trennt die Liste genau
        if lv1:
            sc.min_level = max(2, min(70, int(lv1))) if lv1 is not True else cfg['min_level']
            log(T('Nur Spieler ab Lv.{min} (die Liste ist nach Sternen und Stufe sortiert; bei der ersten Karte darunter hört der Bot auf).',
                  min=sc.min_level))
        if rarities:
            sc.rarities = set(rarities)
            log(T('Nur Spieler der Seltenheit {r} (Manage Players wird dafür nach Seltenheit sortiert, danach wieder nach Deploy).',
                  r=', '.join(sorted(rarities, key=['SP', 'UR', 'SSR', 'SR', 'R', 'N'].index))))
        sc.run_scan(parts)
    except KeyboardInterrupt:
        log(T('Abgebrochen (Strg+C).'))
    except SafetyStop as e:
        log(T('SICHERHEITS-STOPP: {err}', err=e))
    except (BotError, DeviceError) as e:
        log(T('Gestoppt: {err}', err=e))
    except Exception:
        log(T('Unerwarteter Fehler:'))
        log(traceback.format_exc())
    if sc and sc.notes:
        log()
        log(T('Hinweise ({n}):', n=len(sc.notes)))
        for n in sc.notes[:60]:
            log('  - ' + n)
        if len(sc.notes) > 60:
            log(T('  ... und {n} weitere (im Protokoll oben)', n=len(sc.notes) - 60))
    log()
    if os.path.exists(out):
        with open(out, encoding='utf-8') as f:
            d = json.load(f)
        log(T('Gelesen: {what}', what='; '.join(scan_summary(d)) or T('nichts')))
        log(T('Scan-Datei: {path}', path=out))
        log(T('Im CC-Simulator: Menü „Speichern“ → Kasten „HFH-Bot“ → „Scan-Datei laden“ und diese Datei wählen.'))
    else:
        log(T('Es wurde nichts gelesen, darum gibt es keine Scan-Datei.'))
    log(T('Protokoll: {path}', path=log.f.name))


def menu():
    migrate_folders()
    choose_lang()
    print(f'HFH-Bot {VERSION}')
    while True:
        en = texts.LANG == 'en'
        keys = {'view': 'v' if en else 'a', 'delete': 'd' if en else 'l', 'lang': 'l' if en else 's',
                'scan': 's' if en else 'k'}
        items = loadouts()
        print()
        print(T('Gespeicherte Aufstellungen:'))
        if not items:
            print(T('  (noch keine)'))
        for i, (name, path, lo) in enumerate(items, 1):
            if isinstance(lo, Exception):
                print(T('  {i}  {name}: Datei fehlerhaft ({err})', i=i, name=name, err=lo))
            else:
                print(f'  {i}  {lo.summary()}')
        print()
        print(T('  Nummer  Aufstellung im Spiel anziehen'))
        print(T('  p       Probelauf: nur ansehen und berichten, nichts ändern'))
        print(T('  n       Neue Aufstellung aus der Zwischenablage speichern'))
        print(T('  {k}       Aufstellung ansehen', k=keys['view']))
        print(T('  {k}       Aufstellung löschen', k=keys['delete']))
        print(T('  t       Verbindung testen: zeigt den offenen Spieler und liest seine Teile'))
        print(T('  {k}       Account scannen: Spieler, Potentials, Memories, Bonds, Cheers, Coaches für den Simulator', k=keys['scan']))
        print(T('  {k}       Language / Sprache: English', k=keys['lang']))
        print(T('  q       Beenden'))
        c = ask('> ').lower()
        try:
            if c == 'q':
                return
            if c == keys['scan']:
                scan_menu()
            elif c == 'n':
                new_loadout()
            elif c == 't':
                test_connection()
            elif c == keys['lang']:
                lang = 'de' if en else 'en'
                texts.set_lang(lang)
                save_lang(lang)
            elif c == keys['delete']:
                delete_loadout(items)
            elif c in (keys['view'], 'p') or c.isdigit():
                n = c if c.isdigit() else ask(T('Welche Nummer? '))
                if not n.isdigit() or not (1 <= int(n) <= len(items)) or isinstance(items[int(n) - 1][2], Exception):
                    print(T('Keine gültige Nummer.'))
                    continue
                lo = items[int(n) - 1][2]
                show_loadout(lo)
                if c == keys['view']:
                    continue
                dry = c == 'p'
                print()
                print(T('Im Spiel muss bei einem beliebigen Spieler die Seite „Player Potential“ offen sein.'))
                print(T('Während des Laufs bitte nichts im Emulator anklicken. Abbrechen mit Strg+C.'))
                if yes(ask(T('Los? (j/n) '))):
                    run_loadout(lo, dry)
        except KeyboardInterrupt:
            print('\n' + T('Abgebrochen.'))
        except Exception as e:
            print(T('Fehler: {err}', err=e))


if __name__ == '__main__':
    menu()
