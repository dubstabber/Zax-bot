# Zax bot-support — reverse engineering notes

Goal: let the **host** of a multiplayer match add AI bots from an in-game control path.
- Current control: press **B** to open the bot menu, then press a digit.
- Deathmatch (free-for-all): **B → 1** spawns a bot.
- Team games: **B → 1** through **B → 4** spawns a bot on the selected team
  (within that mode's valid team range).
- **R is diagnostic only**: it writes runtime snapshots to `zax_dump.bin` for
  dynamic analysis. It does not spawn bots.
- Future UI: replace the temporary key menu with a custom window/modal for adding bots.
- Target fidelity: a full player-bot (player model, team color, frag-scoreboard entry, AI-driven).

Implemented by binary-patching `Zax.exe` (Reflexive, 2001; "Zax: The Alien Hunter").

## Files
- `Zax.exe` — patch target. **Rebuilt from `Zax.exe.bak` on every `zax_patch.py` run.**
- `Zax.exe.bak` — pristine original. **Never modify.**
- `Zax.exe.i64` — IDA Pro database (of the *original* image; the patched `.zaxbot`
  section is NOT in it). Reached via the IDA MCP server.
- `zax_patch.py` — the patcher (the only artifact we hand-edit). Self-documented.
- `zax_dump.bin` — appendable runtime snapshots written by **R** for dynamic analysis.
- `zax_bot.log` — leftover from the old (crashing) attempt; no longer written.

## How to build & test
```
python3 zax_patch.py        # rebuilds Zax.exe from .bak and applies the patch
```
Then run `Zax.exe` (under Wine), host an MP match, press **B**, then press the
team digit. Use **R** only when collecting a runtime dump. The game runs on a
single main thread that also drives fullscreen DirectDraw, so the hook must avoid
anything fragile on that thread (see `02` — file I/O there crashes under Wine).

## Doc index
- `01-binary-and-patching.md` — PE layout, the `.zaxbot` section, the hook mechanism.
- `02-keyboard-and-message-pump.md` — message pump, the WM_KEYDOWN hook site, the crash.
- `03-multiplayer-and-display.md` — manager chain, `CMultiPlayerGameData`, the on-screen
  message function `sub_59B260`, team/score strings.
- `04-spawn-ai-leads.md` — entity spawn / AI leads for Phase 2/3 (confidence-tagged).

## Current result (working in-game)
Pressing **B** in a hosted MP match opens the bot/team menu; each selected digit spawns one
bot until the active map's **Max Players** cap is reached. Bots are real participants on the
frag scoreboard with visible characters at spawn points, no input mirror, no camera steal,
host fully functional, and no known end-game crash. The display name is still sometimes
uninitialized/gibberish because the DirectPlay name query path is not fixed yet.
The bot is currently a stationary shooter; full navigation/combat remains separate work.
Full recipe + analysis in
`04-spawn-ai-leads.md`; the end-goal vision in the project memory.

## Phase status
- **Phase 0 — patcher restructured.** Done.
- **Phase 1 — crash-free keyboard hook + host/MP gate + on-screen confirmation.**
  Done & verified in-game (no crash, message shows).
- **Phase 2 — spawn player-bot participants** (scoreboard + character at spawn point).
  DONE for stationary bots via DirectPlay queue injection and `sub_59DF90`; now map-capped.
- **Milestone 1 — create scoreboard participant.** DONE (in-game).
- **Milestone 2 — spawn the bot's character in the arena.** DONE (in-game).
- **Milestone 3 — make it an independent AI bot** (stop input-mirror + camera-steal; AI
  movement/combat; map-capped multi-bot indexing; digit-selected team mapping; appearance). TODO —
  the deepest remaining work; the control/AI think + camera/controlled-player mechanism are
  vtable-dispatched and buried under property-registration boilerplate.

## Convention
Addresses are absolute VAs (image base `0x400000`). "Verified" = read directly in IDA
and/or confirmed by runtime behavior. "Lead" = plausible but not yet confirmed — do not
bake into the patch without checking in IDA first.
