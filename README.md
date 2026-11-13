# HFH-Bot

Helper for **HAIKYU!! FLY HIGH** (Global) in **LDPlayer** on Windows.
Hilfsprogramm für **HAIKYU!! FLY HIGH** (Global) im **LDPlayer** unter Windows.

- **Equip Potentials and Memories** exactly as planned in the HFH CC-Simulator, so you can switch between line-ups (e.g. "Rating" and "Score") with one command.
- **Scan your account**: players (level, skills, rating, memory, potentials), spare potentials, memories, bonds, cheers and coaches, saved as a file the simulator can import.

> ⚠️ **Unofficial fan project, use at your own risk.** The game's Fair Play rules forbid third-party software that interacts with the game. Using this bot can get your account penalised.
> ⚠️ **Inoffizielles Fanprojekt, Nutzung auf eigene Gefahr.** Laut den Fair-Play-Regeln des Spiels ist Software verboten, die mit dem Spiel zusammenarbeitet. Dein Konto kann dafür bestraft werden.

## Download
Click the green **Code** button → **Download ZIP**, then unzip it into its own folder.
Grüner Knopf **Code** → **Download ZIP**, dann in einen eigenen Ordner entpacken.

## Quick start / Schnellstart
1. LDPlayer: Settings → Other settings → ADB debugging → **Open local connection**, then restart LDPlayer.
   LDPlayer: Einstellungen → Andere Einstellungen → ADB-Debugging → **Lokale Verbindung öffnen**, dann LDPlayer neu starten.
2. Keep the resolution at 16:9 (e.g. 1600 × 900 or 1920 × 1080).
   Auflösung 16:9 lassen (z. B. 1600 × 900 oder 1920 × 1080).
3. Double-click **start.bat**. The first start sets everything up (1–3 minutes). If Python is missing, it offers to install Python 3.12; then run start.bat again.
   Doppelklick auf **start.bat**. Der erste Start richtet alles ein (1–3 Minuten). Fehlt Python, bietet er an, Python 3.12 zu installieren; danach start.bat noch einmal starten.
4. In the menu, type **t** to test what the bot sees.
   Im Menü **t** eingeben, um zu testen, was der Bot sieht.

Full guide: [README.txt](README.txt) (English) · [LIESMICH.txt](LIESMICH.txt) (Deutsch)

## Files / Dateien
| File | What it does |
|---|---|
| `start.bat` | Sets up Python and the text recognition, then starts the bot |
| `potbot.py` | Menu |
| `bot.py`, `scan.py`, `plan.py`, `layout.py` | Equipping, account scan, planning, screen layout |
| `device.py`, `fastadb.py` | Connection to LDPlayer via ADB |
| `vision.py` | Text recognition (RapidOCR) |
| `texts.py`, `gamedata.json` | Texts (DE/EN) and game data |
| `potbot.ini` | Settings (language, ADB path, speed) |

The bot only uses ADB on your own PC. It sends nothing to the internet except downloading the Python packages in `requirements.txt` on the first start.
Der Bot nutzt nur ADB auf deinem eigenen PC. Er schickt nichts ins Internet, außer dass er beim ersten Start die Python-Pakete aus `requirements.txt` herunterlädt.
