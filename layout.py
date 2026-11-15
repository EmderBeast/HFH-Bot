"""Bildschirm-Positionen in HAIKYU!! FLY HIGH (Referenz 1600 x 900, 16:9).

Gemessen am 06.11.2026 im LDPlayer 14 (Global-Version, englische Oberfläche). Positionen, die über einen Text
gefunden werden können, sucht das Programm lieber per Texterkennung; diese Werte sind die Rückfallebene.
"""

SLOTS = ['I', 'II', 'III', 'IV', 'V', 'VI']

# ---------------------------------------------------------------- Seite "Player Potential"
PP_SLOT = {'I': (1205, 140), 'II': (1069, 203), 'III': (1012, 334), 'IV': (1198, 529), 'V': (1325, 476), 'VI': (1387, 354)}
PP_NEXT = (1550, 790)          # Pfeil rechts unten: nächster Spieler
PP_PREV = (108, 790)           # Pfeil links unten: voriger Spieler
PP_INVENTORY = (1458, 120)     # Knopf "Potential Inventory" oben rechts
PP_CLOSE = (1514, 268)         # freie Fläche: schließt ein Teile-Fenster
PP_NAME = (185, 70, 760, 118)  # Name des Spielers oben links
PP_CIRCLE = (930, 70, 1480, 640)
PP_POPUP = (470, 75, 1430, 885)   # wo das Fenster eines angetippten Teils erscheint
EMPTY_SLOT_BGR = (167, 149, 87)   # Farbe eines leeren Platzes (blauer Kreis)
BACK = (58, 50)                # Pfeil zurück oben links

# ---------------------------------------------------------------- Seite "Potential Inventory"
INV_TAB = {'I': (1004, 140), 'II': (1110, 140), 'III': (1218, 140), 'IV': (1323, 140), 'V': (1424, 140), 'VI': (1532, 140)}
INV_COLS = [1037, 1195, 1354, 1512]
INV_ROWS = [265, 432, 599]
INV_CELL = 132                 # Kantenlänge einer Kachel
INV_LIST = (965, 190, 1590, 685)
INV_FILTER = (1435, 793)       # Knopf "Filter Potential"
INV_CLOSE = (1090, 868)        # freie Fläche unter „Owned“: schließt ein Teile-Fenster (bei (1313, 700) liegt nach dem
                               # Blättern eine Kachel, dann ging ein anderes Teil auf; Test 07.11.2026)
INV_PANELS = (20, 30, 1065, 895)   # wo die Teile-Fenster erscheinen (bis 1.14: 1010, das schnitt die letzte Ziffer ab:
                                   # „+72“ wurde „+7“, Oikawa Platz IV am 11.11.2026)
INV_CIRCLE_SLOT = {'I': (589, 102), 'II': (406, 186), 'III': (327, 375), 'IV': (589, 636), 'V': (778, 559), 'VI': (853, 375)}

# ---------------------------------------------------------------- Filter-Fenster
F_CLOSE = (1396, 139)
F_RESET = (274, 725)           # runder Pfeil links unten: setzt den Filter zurück
F_OK = (1199, 725)
F_SET_ADD = (457, 296)         # "Tap to Select +" bei Set (Fenster ganz oben)
F_LEGENDARY = (764, 427)
F_PANEL = (240, 160, 1400, 790)

# Set-Auswahl (Liste der Sets)
SP_CLOSE = (1293, 149)
SP_OK = (1216, 726)
SP_RESET = (1022, 726)
SP_LIST = (300, 200, 1310, 660)

# ---------------------------------------------------------------- Bereiche, die das Programm nie antippt
# (Recommended Potential, Dismantle Potential, Quick Equip/Unequip, Kauf-Knöpfe an den Währungen)
DANGER = [
    (220, 735, 470, 840),      # Recommended Potential
    (480, 735, 720, 840),      # Dismantle Potential
    (735, 735, 960, 840),      # Quick Equip / Quick Unequip (Inventar)
    (1400, 490, 1600, 575),    # Quick Equip / Quick Unequip (Player Potential)
    (1230, 20, 1320, 85),      # + an der ersten Währung
    (1440, 20, 1540, 85),      # + an Gold
    (140, 760, 370, 870),      # Dismantle & Redeem (Manage Players)
]
# Nur für den Knopf „Unequip“ im Teile-Fenster auf Player Potential: die Sperren dieser Seite (ihr Quick Unequip rechts).
# Die Bereiche des Inventars gelten dort nicht, an deren Stelle liegt bei manchen Plätzen der rote Knopf „Unequip“.
PP_ZONES = [(1400, 490, 1600, 575)]
# Texte, auf die das Programm nie tippt. Set-Namen zählen nicht (in "Precise Block" steckt "lock", in
# "Status Enhancement" steckt "enhance"); das prüft device.danger_text.
# Ein Wort, das so anfängt (MAX, Enhance, Lock, Unlock, Sell, Buy, ...):
DANGER_WORDS = ['max', 'enhanc', 'dismant', 'recommen', 'redeem', 'store', 'shop', 'buy', 'purchas', 'recruit',
                'lock', 'unlock', 'delete', 'sell', 'upgrad', 'levelup', 'exchang', 'gem', 'diamond', 'pay',
                'replace', 'use', 'train', 'obtain', 'switch']
# irgendwo im Text (ohne Leerzeichen):
# (auch abgeschnitten: 'QuickEqui')
DANGER_PARTS = ['quickequ', 'quickunequ', 'dismant', 'recommen', 'purchas', 'levelup', 'enhanc', 'autodeploy',
                'savelineup', 'switchout']

# ================================================================ Account-Scan (gemessen am 06.11.2026)
# Startbildschirm
HOME_PLAYER = (1181, 600)
HOME_TEAM = (1286, 594)
HOME_INVENTORY = (1082, 808)
HOME_GALLERY = (1212, 808)
TITLE = (20, 15, 720, 100)             # Seitentitel oben links ("Manage Players", "Team Lineup" ...)
# Manage Players
MP_GROWTH = (83, 432)
MP_POTENTIAL = (84, 610)
MP_MEMORY = (84, 790)
MP_GRID = (940, 95, 1535, 705)         # Kacheln der Spieler rechts
MP_COLS = [1037, 1221, 1405]           # Mitten der drei Kachel-Spalten (Bilder vom 11.11.2026)
MP_FIRST = (1050, 200)                 # Bild der ersten Kachel (oben links)
# Player Stats (Reiter links; Pfeile unten wie auf Player Potential)
PS_GROWTH = (74, 312)
PS_SKILLS = (74, 531)
PS_RESON = (74, 639)
PS_RATING = (200, 255, 470, 330)       # Zahl unter "Player Rating"
PS_LEVEL = (1100, 120, 1540, 240)      # "Lv. 80" im Reiter Growth
PS_SKILLROWS = (1060, 140, 1510, 720)  # vier Skills mit "Level 1/3"
PS_TOTAL = (900, 130, 1400, 215)       # "Current Total Skill Level: 4" im Reiter Skill Resonance
# Featured Memory (Liste der Spieler links)
FM_LIST_X = 152
FM_LIST_Y0 = 181                       # Mitte des obersten Eintrags
FM_LIST_DY = 154                       # Abstand der Einträge
FM_LIST = (60, 110, 250, 900)
FM_LABEL_X = (15, 255)                 # Name unter den Kacheln (links bis an den Rand)
FM_TITLE = (280, 115, 660, 185)        # Name der Memory (läuft als Laufschrift durch)
FM_PANEL = (1040, 120, 1600, 370)      # "Lv.80/80" und Werte
FM_TIER = (1040, 360, 1600, 720)       # "Memory Tier I" und Wirkung
# Manage Inventory, Reiter Memory
INV_MEM_TAB = (106, 630)
MI_COLS = [354, 505, 657, 808, 959]
MI_ROWS = [185, 334, 483, 631]
MI_GRID = (270, 110, 1010, 720)
MI_NAME = (1060, 110, 1590, 175)
MI_TIER = (1060, 430, 1590, 620)
# Gallery
GAL_BOND = (890, 479)
GAL_PLAYER = (1180, 480, 1400, 600)    # "Player 136/146"
# Player Gallery -> Gallery Rewards: Collection Value (Bilder vom 15.11.2026, 1.18)
GAL_PLAYER_TAP = (1263, 516)           # Kugel „Player“ in der Gallery
PG_REWARDS = (1202, 100)               # Geschenk „Gallery Rewards“ oben rechts
PG_VALUES = (365, 180, 600, 760)       # linke Spalte im Fenster: „Collection Value 8040/8050“ je Zeile
PG_POPUP = (340, 120, 1290, 780)       # das ganze Fenster
PG_CLOSE = (1223, 135)                 # X oben rechts im Fenster
BG_CARDS = (80, 140, 1560, 760)        # Fotos der Bonds in der Bond Gallery
BG_UNLOCKED = (1270, 60, 1560, 140)    # "Unlocked Bonds 142/171"
BG_SCHOOLS = (0, 740, 1600, 850)       # Reiter der Schulen unten ("KARASUNO HIGH SCHOOL" ...)
BG_SCHOOL_X = [171, 460, 753, 1047, 1340]   # Kreise der Schulen (fünf zu sehen, weitere nach Wischen)
BG_SCHOOL_Y = 790
BI_TITLE = (20, 150, 800, 250)         # "The Guardian Deity Lv.5" (+ "Deployment Bond")
BI_RIGHT = (1060, 170, 1600, 820)      # "Unlock Bonus: NAME"
BI_CARDS = (200, 250, 900, 720)        # Namen unter den Karten
BI_NEXT = (909, 823)                   # Pfeil rechts unten
BI_PREV = (133, 823)                   # Pfeil links unten
# Team Lineup und Team Cheer
TL_CHEER = (439, 835)
TC_RB = (40, 160, 320, 280)            # "Rating Bonus" mit Zahl
TC_CARD_X = [311, 512, 713, 914, 1113, 1313]
TC_CARD_Y = 802
TC_CARDS = (200, 700, 1440, 900)
TC_POP_NAME = (270, 580, 640, 660)
TC_POP_TEXT = (600, 200, 1350, 580)
TC_POP_CLOSE = (1356, 117)
TC_COLLECTED = (20, 800, 210, 900)     # "Collected 26/27"

# Coach (Team Lineup -> Coach): links die Coaches, in der Mitte die eigenen Exemplare, rechts die Werte
TL_COACH = (873, 830)                  # Knopf "Coach" auf Team Lineup
CO_TYPE_X = 106
CO_TYPE_Y = [150, 271, 393, 514, 636]  # die ersten fünf Coaches (Liste ganz oben); weitere nach Wischen unten
CO_TYPE_LAST_Y = [718, 597, 476]       # nach dem Wischen ans Ende: von unten nach oben
CO_TYPES = (40, 95, 175, 790)
CO_GRID = (225, 95, 580, 665)          # Exemplare (zwei Spalten), unten endet die Liste über "Dismantle"
CO_GRID_X = [301, 453]
CO_GRID_Y0 = 146                       # Mitte der ersten Reihe
CO_GRID_DY = 114.5
CO_OWNED = (10, 795, 230, 880)         # "Owned: 5"
CO_NAME = (975, 95, 1450, 150)         # "NOBUTERU IRIHATA"
CO_RATING = (1440, 95, 1595, 150)
CO_LEVEL = (955, 148, 1200, 195)       # "Lv.15"
CO_BASIC = (955, 238, 1300, 282)       # "Basic Stats 210"
CO_EG = (955, 315, 1585, 392)          # Expert Guidance
CO_PA = (945, 418, 1588, 732)          # Positional Advantages (blättert)
CO_PA_ICON_X = 984                     # Kreis mit der Position (OP, WS, S, MB, Li)
CO_PA_DOT_X = 1021                     # Punkt in der Farbe der Stufe (rot, orange, lila, blau)
CO_PA_TEXT_X = 1032

# Gesperrte Bereiche je Seite (zusätzlich zu den Währungs-Knöpfen oben). Für den Account-Scan; der Pot-Bot nutzt DANGER oben.
DANGER_TOP = [(1230, 20, 1320, 85), (1440, 20, 1540, 85), (1150, 20, 1330, 85)]
LOCK_PLAYER = (790, 20, 870, 125)      # Schloss oben in der Mitte (Spieler sperren) auf Manage Players / Player Stats
ZONES = {
    # Recruit, Store, Challenge, Werbung, Volleyball Pass, Events/Ranking
    'home': [(1330, 400, 1570, 520), (40, 590, 160, 710), (1130, 220, 1570, 380), (60, 690, 420, 840),
             (20, 280, 170, 370), (160, 590, 380, 700)],
    'manage': [(140, 760, 370, 870), (640, 760, 900, 870), LOCK_PLAYER],  # Dismantle & Redeem, Recommended Lineup
    'stats': [(1230, 750, 1480, 860), (900, 760, 1500, 860), LOCK_PLAYER],  # Upgrade (Skills), Knöpfe unter Growth
    'memory': [(1040, 720, 1310, 815), (1320, 720, 1570, 815)],         # Replace, Enhance
    'manage_inv': [(780, 725, 1050, 815), (1060, 725, 1310, 815), (1320, 725, 1570, 815)],  # Dismantle/Sell, Info, Enhance/Use
    'bond': [],
    # Potential Inventory: Recommended Potential, Dismantle Potential, Quick Equip. Nicht der Quick-Equip-Bereich von
    # Player Potential (1400, 490, 1600, 575): dort liegt im Inventar die vierte Kachelspalte (Scan 07.11.2026).
    'inv': [(220, 735, 470, 840), (480, 735, 720, 840), (735, 735, 960, 840), (140, 760, 370, 870)],
    'team': [(1000, 790, 1600, 900), (130, 760, 360, 900)],             # Auto-Deploy, Player Selection, Save Lineup, Bond
    'cheer': [(1430, 560, 1600, 900)],                                    # Store, How to obtain, Save
    # Schloss am Namen, Dismantle, Filter/Sortieren, Profile/Simple Settings, Replace/Switch Out/MAX/Upgrade, "+"
    'coach': [(915, 90, 975, 150), (290, 665, 470, 735), (215, 770, 545, 850), (800, 650, 950, 860),
              (975, 765, 1580, 855), (1440, 20, 1600, 90)],
}

# ---------------------------------------------------------------- Featured Memory → „Replace“ (Auswahl der Memory, 08.11.2026)
# umgerechnet aus einem Bildschirmfoto (1456 × 819 Fenster, Spielbereich x 38-1392, y 24-782)
FMR_TABS = {'All': (96, 135), 'WS': (242, 135), 'MB': (388, 135), 'S': (534, 135), 'Li': (682, 135), 'OP': (830, 135)}
FMR_TABROW = (60, 105, 960, 165)
FMR_COLS = [83, 234, 386, 539, 690, 843]  # Kacheln, 6 Spalten
FMR_ROWS = [246, 395, 543, 691]           # 4 Reihen sichtbar
FMR_GRID = (30, 175, 900, 745)
FMR_TITLE = (1300, 112, 1592, 212)        # rechts: Name der ausgewählten Memory (links daneben die getragene)
FMR_TIER = (960, 405, 1320, 472)          # "Memory Tier II"
FMR_EFF = (960, 475, 1592, 740)           # Wirkung
FMR_BTN = (1143, 807)                     # Replace / Equip (Enhance rechts daneben bei 1406 nie)
FM_BTN = (1177, 765)                      # Replace / Equip auf Featured Memory
PS_STARS = (195, 180, 450, 232)           # Player Stats: Sterne (Rang) unter der Seltenheit
PS_STAR_DX = 34.2                         # Abstand der Sterne
