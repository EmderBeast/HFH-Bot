HFH-BOT
=======

Two things for the CC simulator, in LDPlayer:
- Equip potentials: equips in the game exactly the potentials that the simulator shows on your
  players. This lets you switch between two loadouts with one command, for example "Rating" and "Score".
- Scan the account: reads your players (level, skills, rating, memory, potentials), your other
  potentials, memories, bonds, cheers and coaches from the game and saves them as a file the
  simulator imports.
(Deutsch: siehe LIESMICH.txt)


ONE-TIME SETUP
--------------
1. LDPlayer: Settings > Other settings > ADB debugging > "Open local connection".
   Then restart LDPlayer once.
2. Keep LDPlayer at a 16:9 resolution (for example 1600 x 900 or 1920 x 1080).
3. Double-click start.bat. The first start sets everything up (1 to 3 minutes). It speaks the
   language of the page you downloaded it from (switch it with "l" in the menu); if potbot.ini has
   none, it asks (e = English). If Python is missing, it offers to install Python 3.12; run
   start.bat again afterwards.
4. Type "t" in the menu: it shows which page and which player it sees in the game and reads
   that player's pieces.


GETTING A LOADOUT FROM THE SIMULATOR (once per loadout)
-------------------------------------------------------
1. Simulator > Potential calculator: mode "Whole team, no piece twice",
   goal "For the rating" (or "For the score"), "Calculate", then "Equip all".
2. Simulator > menu "Save" > box "Export / import" > "Copy".
3. In the bot, type "n", press Enter and give it a name: "Rating" (or "Score").
   You don't paste anything: the bot reads the copied text from the clipboard by itself.

Several teams in one loadout (for example three teams for the rating): after the first team,
answer "Add another team?" with y, open the next team in the simulator (tabs above the court)
and press "Copy" again.

The bot only takes players who wear real pieces in the simulator (after "Equip" or "Equip all").
Players with a template from the Potential guide are listed as "skipped".


SWITCHING
---------
1. In the game: Player > tap any player > Potential. The "Player Potential" page is open.
2. In the bot, type "p" first (preview, a dry run): it reads everything and writes down what it
   would do, but changes nothing.
3. Then type the number of the loadout: now it equips.

While it runs, do not click anything in the emulator. Ctrl+C cancels at any time.
Depending on how many pieces change, it takes 10 to 30 minutes.


SCANNING THE ACCOUNT
--------------------
1. Open the home screen in the game. Switch off any filter in "Manage Players".
2. In the bot, type "s" and choose what to read (Enter = everything):
     1 Players: level, skills, rating, memory and potentials   15 to 30 minutes
     2 Potentials in the inventory (Legendary only)            about 2 seconds per piece
     3 Memories in the inventory                               about 1 second per memory
     4 Bonds (collection bonds and bond levels)                3 to 8 minutes
     5 Cheers and rating bonus                                 1 to 2 minutes
     6 Coaches: level, rating and Positional Advantages        2 to 4 minutes
3. It goes through the pages by itself. At the end the file is in the "scans" folder
   (hfh-scan-DATE.json). If it stops (Ctrl+C or an error), everything read until then is in it.
4. Simulator > menu "Save" > box "HFH-Bot" > "Load scan file".
   The file goes into "My account". Whatever it does not contain stays as it is in the simulator.

The scan only opens pages, tabs and windows and scrolls through lists. It does not buy, enhance,
sell, replace or save anything. Whatever it could not read for sure is listed at the end under
"Notes" (and in the log): that stays as it was in the simulator or is missing from the file.
It does not read which cheers are deployed in the game; those stay as they were in the simulator.
Of the coaches it reads every one from level 3 up (with Positional Advantages); of a coach you only
have at level 1 or 2, one.


EXERCISING BOT (Free Time > Exercising)
---------------------------------------
1. In the game: Free Time > Exercising. The "Exercising Courses" window is open (or a run that is
   already going).
2. In the bot, type "e", choose the course (Enter = Marathon) and how many runs (Enter = all that
   "Exercising Left" still allows).
3. It rolls (GO!), first heads for the finish flag at forks, takes the
   right button on the goal field (keep going, so the Action Points are not lost) and then goes away from the goal, answers story questions (answer A, can be
   changed in potbot.ini), closes events and rewards, and collects the reward with "Claim & Exit" at
   the end. It stops at "Exercising Left: 0".
At forks it reads the fields on every way (story and gift = diamonds, arrow = +1 Action Point; banana peel =
-1 Action Point, question mark = a card is lost, hand with sweat = Stamina Drain) and works out with the possible
rolls which way brings the most. Before the goal, the goal pulls harder the fewer Action Points are left. Cards:
Lightweight Runners always; Sports Drink when good fields are only 4 to 6 fields away; Energy Candy with many bad
fields ahead, when the Action Points for the goal run short, or at the very end; Vitamin C Jell-O against
Stamina Drain before the goal. It leaves Reverse and Hitchhiking.
In the shop it always picks "Don't Buy". It buys nothing, not even with Action Points.
Settings in potbot.ini: exercising_kurs (1 to 4), exercising_story (a, b or zufall = random),
exercising_karten (ja = yes or nein = no), exercising_ziel (einmal = to the goal once, continue, then away;
meiden = always away; ziel = always towards it; zufall = random).

WHAT IT DOES WHEN EQUIPPING
---------------------------
- It goes through your players with the arrows and reads every piece of the loadout's players.
- Pieces that another player of the loadout needs are unequipped first.
- It looks for the other pieces in the Potential Inventory (filter: set and Legendary) and equips
  them. After every "Equip" it checks that the right piece is in place.
- If a player outside the loadout wears a piece it needs, it takes the piece from that player.
- At the end it reads all changed players once more.
- Everything goes into the log in the "logs" folder, with screenshots when something goes wrong.


MEMORIES
--------
After the potentials it goes through the line-up's players under Player > Memory ("Featured Memory").
If one doesn't wear the memory from the simulator (same memory, same tier), it taps "Replace", looks
for it in the selection and taps "Replace" there. Then it checks that it sits.
- It prefers a free copy (no small face in the top right of the tile in the selection).
- If two players of the line-up should wear the same memory, it only takes free copies. If you own it
  only once, one gets it and the other shows "no free copy" in the result.
- Players at Lv.1 don't appear under Featured Memory and get none.

WHAT IT NEVER DOES
------------------
It taps "Replace" only on the Featured Memory page, to equip a memory.
It never presses MAX, Enhance, Dismantle, Quick Equip, Quick Unequip, Recommended, Lock or anything
to buy, and it never spends currency. Before every tap it reads what is at that spot and stops
right away if one of those buttons is there.


GOOD TO KNOW
------------
- Menu: number = equip, p = preview, n = new loadout, v = view, d = delete, t = test,
  s = scan the account, l = language (Deutsch/English), q = quit.
- On the scan's pages (Player Stats, Featured Memory, inventory, Bond Info, Team Cheer with an open
  window, Coach), "t" also shows what it reads there.
- Loadouts are saved in the "loadouts" folder, scans in "scans", logs in "logs".
- Afterwards the filter in the Potential Inventory stays on set and Legendary.
- The game language must be English.
- If something goes wrong: send the log and the screenshots from "logs".
- potbot.ini: language, path to adb.exe (if it is not found) and which LDPlayer (if there are several).
- Games usually do not allow helper programs like this in their terms of service.
  Use it at your own risk.

NEW IN 1.4 (07.11.2026)
-----------------------
- Account scan: duplicate cards in "Manage Players" (several cards of the same player) no longer end
  the scan. It skips them and reads every player, as many as the Gallery counts.
- Memories in the inventory: the tier ("Memory Tier") is now also read for memories with three stat rows.
- Memories of the players (Featured Memory): the list is read with the correct spacing.

NEW IN 1.5 (07.11.2026)
-----------------------
- Account scan: optionally only SP, UR and SSR players and memories (asked at the start). Manage Players is sorted by
  rarity for this and back to Deploy afterwards. The simulator then keeps SR, R and N as you set them.
- Potentials in the inventory: now reads the whole list (scrolls on the tiles) and locked pieces (status Locked);
  only pieces at +12 count.
- Players: skills that still need training count as 0; the level at the level cap ("Lv. 70 (MAX)") is read.
- Memories of the players (Featured Memory): the list loops and the names scroll; the bot now finds the players by
  their names. Uncertain memories stay as they are in the simulator.
- An open piece window is closed on an empty spot (before, a tile lay there after scrolling).

NEW IN 1.6 (08.11.2026)
-----------------------
- Potentials in the inventory: now counts exactly (checked in the game: all six slots match). It only swipes on a
  tile (between two rows the list does not move), loads the whole list first, drags evenly without coasting and
  counts two identical pieces as two. If a piece does not open, it taps it again.

NEW IN 1.7 (08.11.2026)
-----------------------
- Player memories: now read reliably (everything it read in the test was right). It walks the "Featured Memory" list tile by tile
  (it is sorted by level), checks by the golden frame that the next card really is selected, and reads the scrolling
  name until it knows the version (the part in brackets) for sure. It stops at the first card at Lv.1; players at
  Lv.1 get no memory.

NEW IN 1.8 (08.11.2026)
-----------------------
- Equipping memories: a line-up now also carries each player's memory; the bot equips them after the potentials
  (see MEMORIES).

NEW IN 1.9 (10.11.2026)
-----------------------
- Potentials: right after "Equip" an animation runs in the circle. The check now waits for it and taps up to three
  times; before, the bot stopped although the piece sat right (Kuroo, slot I, in the run "Peak").
- If the potential part does stop, the result says "Potentials NOT finished" instead of "Nothing to do".
- Memories: the bot sees in the selection which copies someone already wears and takes a free one first. If two
  players should wear the same memory and you own it twice, both get one now (before, neither did).

NEW IN 1.10 (11.11.2026)
------------------------
- Account scan, potentials of the players: if the row of a sub stat also holds a foreign text ("+4% +12"), the value
  right after the name counts now. Before, the piece was missing in the scan file (slot VI of three players).
- Account scan, bonds: "SEIJOH's Senior Year Pillars" is now recognised as "AOBA JOHSAI's Senior Year".

NEW IN 1.11 (11.11.2026)
------------------------
- Equipping memories: the selection after "Replace" is sorted by level. The bot now reads the "Lv.N" labels under the
  tiles and stops at the first memory at Lv.1 instead of tapping every single one. Memories at Lv.1 are no longer
  searched (in the log: "only Lv.1, search stopped").
- Account scan, memories in the inventory: also stops at the first memory at Lv.1. Memories at Lv.1 are then not in
  the scan file.
- Equipping memories: the bot always searches the "All" tab from left to right, no longer the WS, MB, S, Li and OP
  tabs.

NEW IN 1.12 (11.11.2026)
------------------------
- Potentials: if a piece or a player does not work, the bot reports it as open and goes on with the next one. Before,
  it stopped the potentials of all remaining players.
- Potentials: the bot now opens "Player Potential" itself through "Manage Players". This way the arrows go through all
  players, not only those of the lineup in the game. If the arrows still show only a few players, the log says so
  as a WARNING.
- Equipping memories: the bot reads the selection after "Replace" only once and remembers which memory lies where. For
  the next player it taps the remembered tile directly and checks it. Rows still visible after scrolling are no longer
  read twice (before, such copies were counted twice).
- Account scan, memories: the scan file additionally contains "memoryCopies": every memory with tier, level and
  count. "memories" stays as before.

NEW IN 1.13 (11.11.2026)
------------------------
- Unequipping potentials: for some slots the red "Unequip" button lies where "Quick Unequip" sits in the inventory, so
  the bot stopped with a safety stop. On "Player Potential" it may now tap the red button. "Quick Unequip" stays
  blocked, and it only taps where the text is exactly "Unequip".
- Finding players: duplicate cards stand next to each other and look the same. The bot took the first duplicate for
  the end of the list and did not find players behind it. Now it keeps tapping past duplicates.

NEW IN 1.14 (11.11.2026)
------------------------
- Finding players: the bot no longer taps past duplicate cards with the arrows (with 1385 cards that took over an
  hour). A player the arrows do not reach is now looked up in "Manage Players": the bot reads the name strips on the
  tiles, taps only matching ones and opens "Player Potential" directly at that player.

NEW IN 1.19 (15.11.2026)
- New: Exercising bot (menu "e"). Plays the runs in Free Time > Exercising: rolling, forks, story questions,
  events, helpful cards, "Claim & Exit". Always "Don't Buy" in the shop. See "EXERCISING BOT" above.

NEW IN 1.18 (15.11.2026)
- Account scan, bonds: it also reads the Collection Value of your Player Gallery (Gallery -> Player -> Gallery
  Rewards, e.g. 8040) and writes it into the scan file. The simulator sets the rule "Player Gallery: Collection
  Value" from it. It only taps the gift "Gallery Rewards" and the X there, never "Claim".

NEW IN 1.15 (11.11.2026)
------------------------
- Faster: the screenshot now comes raw from the emulator (0.4 instead of 0.85 seconds), the bot reads the page title
  and the player name as one line (0.03 instead of 0.3 to 0.7 seconds), and the pauses after a tap are shorter
  (potbot.ini: tempo = 0.7; 1.0 = as before).
- Inventory filter: only set + Legendary + status, no main stat. The same filter then serves all slots and players
  with this set; before, the bot set it up again for almost every piece.
- Equipping: with a long set text the window's red "Equip" button lies where the page has "Quick Unequip"; the bot
  stopped with a safety stop. With "MAX" to its left it is the window's button, and the bot may tap it.
  "Quick Equip" and "Quick Unequip" stay blocked.
- At a safety stop the bot saves a screenshot in logs\screenshots.
- Memories: the bot no longer looks for the players through the scrolling names on the left of "Featured Memory"
  (about 10 seconds per tile, and it mixed up Sugawara and Nishinoya). It goes to the player on "Player Potential",
  then "Back" and "Memory": the game opens Featured Memory exactly at this player.
- Memories: the selection after "Replace" only shows memories for the player's position. The bot therefore remembers
  it per position; before, it looked for a WS memory in the selection of a libero.
- Players who only get a memory (no pieces) are now searched too.
- Piece window: text of the page behind it no longer ends up in the substats (Sugawara, slot III).
- Locked slots (grey with a lock, e.g. on SR cards) count as empty; an unreadable slot no longer stops the run.
- If a piece is missing, the bot first tries all slots of the set with the filter "locked", then the missing ones
  with "unlocked".
- Player list: for its run the bot sorts "Manage Players" by level (high to low) and sets "Deploy" again at the end.
  All levelled players are then at the front, and the arrows reach SR cards like Watari too (before: up to 14 minutes
  of searching the tiles).
- Set filter in the inventory: 16 to 21 instead of 16 to 33 seconds. The order of the sets in the selection is known;
  the bot scrolls close to the set without looking and remembers the buttons in the filter window.
- Inventory: the reading frame cut off the last digit on the right ("+72" became "+7"); right pieces counted as wrong.
- With a minimum level (potbot.ini: min_level, 2 to 70) the account scan reads every player from that level on and
  each player's memory along the way; the scan file has the count of every memory.
