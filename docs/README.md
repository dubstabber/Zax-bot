# Zax bot-support notes

Goal: let the **host** of a multiplayer match add bot participants to
*Zax: The Alien Hunter* by runtime-patching the original `Zax.exe`.

Current control path:
- **B** opens the bot menu in a hosted MP match.
- A digit selects the spawn/team option for the current mode.
- **R** appends a diagnostic runtime snapshot to `zax_dump.bin`; it does not spawn.

## Source of truth

- `Zax.exe.bak` is the pristine original. Never modify it.
- `Zax.exe` is rebuilt from `Zax.exe.bak` every time `python3 zax_patch.py` runs.
- `zax_patch.py` is now a thin entrypoint. The actual patch lives in `zaxbot/`:
  - `zaxbot/config.py` - section size, scratch layout policy, bot ids/names.
  - `zaxbot/addresses.py` - original-image VAs and verified prologues.
  - `zaxbot/patch_manifest.py` - enabled hook/detour sites.
  - `zaxbot/hook/` - dispatcher, mode detection, spawn, snapshot.
  - `zaxbot/detours/` - prologue detours for capture, safety, control, fire/aim.
- `Zax.exe.i64` is an IDA database for the original image only. It does not contain
  the appended `.zaxbot` section.

## Build and test

```bash
python3 zax_patch.py
```

Then the user runs the game under Wine, hosts an MP match, and exercises B/R.
Do not launch the game from automation; runtime testing is user-owned.

## Current result

The working path is **Phase B: synthetic DirectPlay queue injection**. On spawn,
the hook injects a synthetic "player added" event, calls the engine's own
`sub_480800` join handler, then calls `sub_59DF90` to create and place the
character. The bot is a real participant: it appears on the scoreboard, has a
visible character, takes damage, can be killed, registers kills, and is broadcast
to a second PC.

Current limitations:
- Mode detection is still stubbed to Deathmatch (`detect_mode` returns 0 after a
  one-shot `mpd[0..0x200]` dump).
- Bots do not navigate. They keep a real walking controller for idle animation,
  but `detour_542360` zeroes their movement vector.
- Bots can fire/aim at the host within range and line of sight via `detour_5436F0`.
- Host-side bot names are set after spawn; PC2 still does not reliably see the
  chosen name because the synthetic DirectPlay player-data store is not populated.

## Doc index

- `01-binary-and-patching.md` - PE layout, `.zaxbot`, patch manifest, build checks.
- `02-keyboard-and-message-pump.md` - WM_KEYDOWN hook and main-thread constraints.
- `03-multiplayer-and-display.md` - manager/session/participant anchors and messages.
- `04-spawn-ai-leads.md` - current Phase B spawn architecture and remaining AI work.

Addresses are absolute VAs for image base `0x400000`. Prefer runtime dumps over
static guesses when validating new multiplayer or bot behavior.
