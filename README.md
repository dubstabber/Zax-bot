# Zax Bot Mod

Multiplayer bots for **Zax: The Alien Hunter** (Reflexive Entertainment,
2001). The game ships with working DirectPlay multiplayer but no bots —
this mod adds them by rebuilding a patched `Zax.exe` from your original
one. Bots are real participants: they show up on the scoreboard, take and
deal damage, are visible to joining clients, and actually play the mode —
Deathmatch, Capture the Flag (attack/defend roles, flag running, carrier
chasing/escaping) and Salvage King (mineral collecting, bin deposits,
death-pile stealing).

Only the **host** of a multiplayer match can add bots. Other players join
normally and don't need the mod (they'll see the bots as regular players).

## Dependencies

- **Zax: The Alien Hunter** — an installed copy of the game. The patcher
  needs the original `Zax.exe` and reads the game's `Data.dat` (and its
  level data) at build time; both are part of every install.
- **Python 3** — version 3.9 or newer (developed and tested on 3.14).
  Standard library only, nothing to `pip install`.
- **Windows** — the game is a Windows program; the mod is developed and
  tested on Windows 11. It has also been run on Linux under Wine.

## Install

All of this happens inside the game's install folder (the one containing
`Zax.exe` and `Data.dat`).

1. **Rename the original `Zax.exe` to `Zax.exe.bak`.** This is your
   pristine backup — the patcher refuses to run without it, rebuilds the
   playable `Zax.exe` from it every time, and never modifies it.
2. **Copy this repository into the game folder**, so that `zax_patch.py`,
   the `zaxbot/` package and the `waypoints/` folder sit next to
   `Zax.exe.bak` and `Data.dat`. (`waypoints/` contains the authored
   navigation graphs for 17 multiplayer maps — without a map's graph,
   bots on that map will stand idle.)
3. **Run the patcher:**

   ```bash
   python zax_patch.py
   ```

   It copies `Zax.exe.bak` to `Zax.exe` and writes the patched image
   (you'll see a summary like `patched: hook_entry @ VA 0x71a000 ...`).
4. **Play.** Launch `Zax.exe`, host a multiplayer match (any mode), and
   press **B** to open the bot menu:
   - DM / Salvage King: **Add Bot**
   - CTF: **Add Blue Bot** / **Add Red Bot**

   The menu stays open so you can add several bots (up to the map's player
   limit, at most 16); close it with **Close**, **Esc**, or the X box.

## Uninstall

Delete the patched `Zax.exe` and rename `Zax.exe.bak` back to `Zax.exe`.
Nothing else in the game install is modified.

## Other in-game keys (host, multiplayer)

| key | action |
|---|---|
| `B` | open/close the bot menu |
| `R` | append a diagnostic runtime snapshot to `zax_dump.bin` (for debugging; harmless) |
| `O` | toggle the waypoint-graph overlay (authoring/debugging; costs FPS while visible) |
| `N` / `J` / `X` / `,` | with the overlay visible: drop a node / select nearest / delete nearest / save the graph to `waypoints/<map>.zwpt` |

## Tuning and rebuilding

All behavior knobs (bot names/colors, movement, CTF roles, dodge
strafing, pickup need-gating, ...) live in `zaxbot/config/`. After
changing anything, re-run `python zax_patch.py` and restart the game —
the patch is rebuilt from `Zax.exe.bak` from scratch every time, so the
build is always deterministic.

`python -m unittest tests.test_patcher` runs the offline test suite
(needs `Data.dat` present for the map-census tests).

## Known limitations

- Bots can only be added by the host, in multiplayer matches.
- A joining client sees bots with default player names (the synthetic
  DirectPlay player-data store isn't populated for remote clients yet);
  the host sees the picked names.
- Maps without an authored `waypoints/` graph get idle bots — you can
  author a graph in-game with the overlay keys above.

## For developers

`AGENTS.md` is the detailed project state (architecture, every behavior
layer, engine addresses, constraints). `docs/` holds the
reverse-engineering notes; `docs/README.md` is the index. The
`docs-extraction-reference-only/` folder documents the game's asset
formats and is not needed to build or run the mod.
