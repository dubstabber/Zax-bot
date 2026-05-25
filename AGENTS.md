# AGENTS.md — Zax bot-support binary patch

This project adds **AI bot support** to *Zax: The Alien Hunter* (Reflexive
Entertainment, 2001) — a fullscreen-DirectDraw, single-thread C++ game — via
runtime patching of `Zax.exe`. The game has working multiplayer (host + client
over DirectPlay) but no bots; we add them host-side by binary-patching new
sections + prologue detours into the unmodified PE.

## Read first

The detailed RE notes live in **`docs/`** — read them before deep work:
- `01-binary-and-patching.md` — PE layout, the `.zaxbot` section, hook mechanism.
- `02-keyboard-and-message-pump.md` — WM_KEYDOWN hook site, **syscall-crash lesson**.
- `03-multiplayer-and-display.md` — manager chain, `sub_59B260`, MP class strings.
- `04-spawn-ai-leads.md` — spawn / AI / participant architecture (largest, most
  important file — contains the long history of what worked and what didn't).

## Files

- **`Zax.exe`** — patch target. **Rebuilt from `Zax.exe.bak` on every run of
  `zax_patch.py`.**
- **`Zax.exe.bak`** — pristine 2001 original. **Never modify.**
- **`zax_patch.py`** — the sole hand-edited artifact. Structure: constants →
  `Asm` (tiny label-based x86 emitter) → `build_hook` (the payload) →
  `patch_pe` (PE surgery: appends a new section, rewrites the call site,
  installs prologue detours).
- **`Zax.exe.i64`** — IDA Pro database of the **original** image. The patched
  `.zaxbot` section is NOT in it. The IDB is loaded in IDA Pro and exposed via
  the IDA MCP server (`mcp__ida-pro-mcp__*`).
- **`zax_dump.bin`** — appendable runtime memory dump. Each chunk has a
  28-byte tagged header: magic `'ZAX1'` | tag (16B ASCII, zero-padded) |
  src_va | len | payload[len]. Pressing **R** in a live MP match appends one
  snapshot. Per-snapshot chunks: `snap`, `mgr_root`, `session`, `worldmgr`,
  `dpmgr`, `idx_nbhd`, then per participant `part[i]` + `stats[i]` (16 B of
  `*(part+0x1C)`) + `cstr[i]` (16 B of the stats CString header), then
  `charptr` (64 B of `mgr+0x290`'s pointer array), then `char[i]` for slots
  0..15. Parse + diff offline with `tools/diffdump.py`.
- **`tools/diffdump.py`** — parser/comparator for `zax_dump.bin`. Subcommands:
  `list`, `snap [N]`, `within N TAG1 TAG2`, `across N1 N2 TAG`, `hexdump`.
  Primary use case: diff `part[remote]` vs `part[bot]` after PC2 joins +
  bot is spawned, to surface participant-classification fields that cause
  the audio leak / future routing bugs.
- **`zax_step.log`** — single-byte progress markers
  (`A/M/2/B/C/D/S/T/P/E/V/N/F`) written one syscall-cycle each by the hook
  during a spawn. Tail to see how far the spawn flow got.

## Build & test

```bash
python3 zax_patch.py        # idempotent: rebuilds Zax.exe from .bak and patches
```

Then the user runs `Zax.exe` under Wine, exercises the feature, and reports.
**Do not launch the game from Agent** — the user owns the runtime loop.

## Current state

**Working (Phase B — DP-queue injection):**
- WM_KEYDOWN hook at `0x599A1A` (`call sub_599580` → `call hook_entry`).
- `.zaxbot` section at VA `0x71A000`, raw `0x231000`, 1 page, RWX.
- Press **B** in a hosted MP match → on-screen prompt via `sub_59B260`.
- Press digit `1`..`4` (within mode's range) → bot spawns via the engine's
  own DirectPlay join handler `sub_480800`:
  1. Inject a synthetic "player added" event into the DP queue at
     `dpmgr+0x44D` (added flag at `[edi-1]`, id at `[+3]`, output ptr
     at `[+7]`; synthetic id range `0xBADC0DE0..0xBADC0DEF`).
  2. Set the `[dpmgr+0x38]`/`[+0x39]` poll gates and the `[+0x8FC]`
     changed flag, then call `sub_480800` directly with
     `edi = host_char_ptr` (= `*(worldmgr+0x290)+0`) so its `var_1C`-based
     name-block derefs land on a stable engine entity.
  3. Read the new participant pointer back from `[my_queue_slot+7]`.
  4. Call `sub_59DF90(mgr, cap_a2, botidx, 0, 0)` to create the character.
- Bot is a fully-classified remote participant: visible character, takes
  damage normally, **no hurt-SFX leak on host**, kills register on the
  scoreboard, broadcast over DP so PC2 sees the bot.
- **Bot has a real display name** (random pick from `BOT_NAMES` table in
  `zax_patch.py`). After `sub_59DF90` we call `sub_4E1930` on the stats
  CString at `*(part+0x1C)` to overwrite the engine's default "Anonimowy"
  with one of the pool entries. ASCII parallel table at `scratch+0xB80`.
- **Up to MaxPlayers bots supported** (16 on a 16-player map). The engine
  reallocates its char array `mgr+0x290` from initial small size to
  capacity ≥ 9 when PC2 naturally joins; bot-only spawns never triggered
  that grow, so the 9th `sub_4EF900` write went OOB and corrupted a
  downstream entity-observer at `0x019446fc` (vtable `0x5F278C`), which
  faulted the next frame in `sub_520940`. Fixed by pre-growing the array
  ourselves: right before each `sub_59DF90`, if `mgr+0x298 < 16` we
  `operator new(64)`, `rep movsd` existing entries in, `operator delete`
  the old buffer, set `mgr+0x290` to the new buffer and `mgr+0x298 = 16`.
  The realloc persists across matches in the same game run (matches the
  natural engine behavior).
- **`bot_*` scratch arrays cleared on match change.** `detour_df90`
  compares the incoming `a2` to the cached `cap_a2`; if they differ a new
  match has started and we `rep stosd` the 64 contiguous dwords at
  `bot_participants_va..bot_controllers_va+64`. Without this clear, stale
  controller pointers in `bot_controllers_va` make `detour_542360` falsely
  zero the host's movement vector in subsequent matches.
- Team binding via `sub_5BA820` for non-DM modes.
- See `[[phase-b-success]]` memory for the bug-hunt history (why `edi`
  had to be host_char specifically, etc.).

**Stub:**
- **Mode detection is hard-coded to 0 (DM).** `detect_mode` dumps
  `mpd[0..0x200]` to `zax_dump.bin` once per session and returns 0. An earlier
  scan-and-deref approach crashed on `0x6E6F6E00` ("non\0") that passed a
  range check; range checks alone do NOT make arbitrary derefs safe. To
  complete: dumps from each mode → find the offset in `mpd` where the
  game-type pointer lives → compare its `*P` (vtable) against the three known
  constants below.

**Deferred / known unfinished (see `04-spawn-ai-leads.md` for details):**
- Bot stands still — **no driver feeding it movement input**. Idle
  animation works (the bot gets a real `CPlayerWalkingControlAI` and
  `detour_542360` zeroes its movement vector to keep it stationary
  without stealing the host's camera/input). Auto-respawn after death
  also works. Movement animation is untested because the bot never
  moves; expected to work once a driver is wired in. The remaining work
  is wiring an AI driver — see "Stage A mismatch" and "Movement requires
  a nav move-component" in `04-spawn-ai-leads.md` for the two known
  paths (controller-with-AI-input vs monster-AI-on-player-body).
- PC2 doesn't see the bot's chosen name. The host writes the name into its
  own participant's stats CString, but DirectPlay's player-data store for
  the synthetic id never gets populated, so PC2's own `sub_480800` falls
  through to the engine's name-block which renders uninit bytes for the
  bot. Cosmetic on the remote; fixing would need a `SetPlayerData` hook or
  re-enabling `detour_name_query1/2` (currently disabled).
- (Previously: bot hurt-sound on host was patched with a mute scope around
  `sub_48D380` + several source-root filters. Phase B's natural participant
  classification makes those band-aids unnecessary — `detour_48D380`,
  `detour_48D5A7`, `detour_543830_call`, `detour_4F5D80`, the source-root
  filters in `detour_4EA880`/`detour_4EAA60`, and `bot_sound_mute_va`/
  `last_sound_root_va`/`s4f5d80_*`/`diag_filter_*` scratch all deleted.)

## Architectural constraints — DO NOT VIOLATE

1. **Single main thread drives DirectDraw.** No blocking calls in the hook
   hot-path. File I/O (CreateFileA/WriteFile/CloseHandle) is *demonstrated
   safe* for diagnostics but never put it in code that runs every frame /
   every keystroke. The previous logging build crashed because of synchronous
   `WriteFile` calls on every B press; the fix was to use `sub_59B260`
   instead. See `02-keyboard-and-message-pump.md`.
2. **Hand-encoded ModR/M bytes are the #1 hazard.** A wrong `FF 51 08` vs
   `FF 52 08` once made EIP land at `1`. When emitting `call [reg+disp]`,
   double-check the reg field (`ecx`=001 → `FF 51`, `edx`=010 → `FF 52`).
   Prefer the `Asm` helpers for control flow.
3. **Address-range checks do NOT make `mov eax,[eax]` safe.** The
   `0x6E6F6E00` crash. Deref only known-safe offsets, or wire in
   `IsBadReadPtr` / SEH first.
4. **Never modify `Zax.exe.bak`.** Always rebuild from it.
5. **The IDB doesn't contain `.zaxbot`.** When inspecting hook bytes, read
   `Zax.exe` directly at file offset `0x231000` (= VA `0x71A000`).

## Hook architecture

```
WM_KEYDOWN at 0x599A1A: call sub_599580   →  patched: call hook_entry (.zaxbot)
                                                          ↓
hook_entry — IDLE/MENU_OPEN state machine on [menu_state]:
  IDLE:
    if cl != 'B': jmp sub_599580                         ; pass-through
    pushad; MP-gate (mgr→vtbl[0x184]→ +0x30 != 0)
    call detect_mode                                      ; eax = 0/1/2 (stub: 0)
    push -1; push prompts_table[eax]; call sub_59B260     ; show prompt
    [menu_state] = 1; popad; jmp sub_599580
  MENU_OPEN:
    pushad
    if cl in '1'..'4' and (cl-'1') < max_for_mode[mode]:
      [chosen_team] = cl - '1'
      call do_spawn_with_team
    [menu_state] = 0; popad; jmp sub_599580

do_spawn_with_team:
  MP gate, DP-mgr/a2 captured?, cap_players check
  find_free_bot_slot                                       ; 0..15
  EnterCriticalSection — DP-queue injection:
    write synthetic id to dpmgr+0x44D[slot] + flags
    call sub_480800 (ecx=dpmgr, edi=host_char)            ; engine creates participant
    read participant back from queue slot, clear queue
    [bot_participants_va[slot]] = botp
  LeaveCriticalSection
  if mode != 0:  ecx=botp; call sub_5BA820 → [eax+0x14] = chosen_team
  pre-grow mgr+0x290 char array to capacity 16 if needed   ; see "Up to MaxPlayers"
  bot_mode = 1
  a2 = sub_4F1050(mgr) or fallback to cap_a2
  sub_59DF90(mgr, a2, botidx, 0, 0)                        ; create+place char
  bot_mode = 0
  bump mgr+0x294 (count) if needed
  set bot name: sub_4E1930(*(botp+0x1C), bot_names_ascii[rand])
  capture botchar into [bot_chars_va[slot]]
  sub_59B260("bot: ..."); ret

prologue detours (rewrite first 5–6 bytes of target → jmp to trampoline):
  sub_480BD0 (DP poll)    : captures DP manager (ecx) → [cap_dpmgr], edi → [cap_dp_edi]
  sub_59DF90              : captures a2 ([esp+4]) → [cap_a2]; if a2 differs from
                            the previous (new match), wipes the four bot_* scratch
                            arrays (64 dwords at scratch+0x180..0x280)
  sub_59BE20              : when bot_mode==1, returns "xor eax,eax; ret 8"
                            (skips creating the human walking controller)
  sub_5AA4E0/sub_4FBC50   : NULL-tolerant component attach for bot
  sub_542360              : zero movement vector for controllers in bot_controllers_va
                            (keeps bot animation idle, no input/camera mirror)
  sub_542550              : called on every controller "Player Num" init. Two paths:
                            (a) when botmode==1 (our spawn): capture ecx into
                                bot_controllers_va[active_bot_slot].
                            (b) when botmode==0 (host/engine-respawn): scrub stale
                                entries matching ecx, AND if [esp+4] (the player
                                index arg) matches any bot_indices_va[i], capture
                                ecx into bot_controllers_va[i]. The capture-by-
                                index branch is what restores the custom
                                fire/aim hook (sub_5436F0) after a bot's natural
                                respawn — otherwise the engine creates a new
                                walking controller at a fresh heap address and
                                bot_controllers_va keeps the stale old pointer,
                                so sub_5436F0 stops recognizing the respawned
                                bot and it sits idle.
  sub_5436F0              : custom fire/aim hook. Scans bot_controllers_va for
                            the current controller; if found, computes distance
                            to host and synthesizes fire + aim angle when within
                            fire_range_sq. Returns AL=0/1 for "fire?" — the
                            engine's downstream code treats AL=1 as Primary
                            trigger pressed. This is what makes bots shoot at
                            the host: the original Zax has no such logic.
```

## `.zaxbot` layout

| section offset | contents |
|---|---|
| `0x000` | `hook_entry` (currently ~4065 B). Code budget = `SCRATCH_OFF = 0x1000` = 4 KB. |
| `0x1000` | scratch — filenames, capture vars, menu state, prompts, bot tables, snapshot tag templates, ASCII bot names at `+0xB80`. See `build_hook` for the precise sub-offsets. |
| total | `NEW_SECTION_SIZE = 0x2000` = 8 KB (2 pages, RWX). |

## Anchor addresses (verified)

| Symbol | What |
|---|---|
| `dword_713F14` | game/world manager pointer; `*dword_713F14` is vtable |
| `mgr->vtbl[0x184]` | active level getter; `level + 0x30` = `mpd` (CMultiPlayerGameData), NULL outside MP |
| `dword_6C2080` | entity/player container; `vtbl[176]` = player list |
| `dword_713F18` | session/participant container (vtable `0x602fa8`) |
| `sub_5BA790` | `__stdcall participant_factory(connId)` — new 280B participant |
| `sub_5BA820(p)` | → stats (auto-syncs via `dword_6C2080->vtbl[4]`) |
| `sub_51F440(p,i)` | → stats (no sync; underlying) |
| `sub_59B260` | `__stdcall on_screen_msg(text, type)` — type=-1 broadcast |
| `sub_59DF90` | per-player char create+place: `(mgr, a2, idx, name, a5)`; retn 0x10 |
| `sub_480BD0` | DirectPlay per-frame poll; `ecx` = DP manager |
| `sub_480530` | DP-free local-participant template recipe |
| `sub_59BE20` | creates the human `CPlayerWalkingControlAI` — skip for bots |
| `sub_42E150` | allocator/ctor for `CApproachTargetAI` (currently unused) |
| `sub_4FBC50` | `__thiscall(char, comp)` — attach component to char (ret 4) |
| `sub_4E1930` | `CString::operator=(this, char*)` — refcounted CString assign |
| `sub_4F1050` | `__thiscall(mgr) → active char ptr` (read-only getter; returns 0 if no active) |
| `0x5D034A` | `operator new(size)` — `__cdecl`, returns ptr in eax |
| `0x5D0330` | `operator delete(ptr)` — `__cdecl`, used for char-array free |
| **`VT_DM_VA  = 0x5F0D54`** | CDeathMatchGameType vtable (from `sub_478D30`) |
| **`VT_CTF_VA = 0x5EF544`** | CCaptureTheFlagGameType vtable (from `sub_468F30`) |
| **`VT_SK_VA  = 0x5FED48`** | CSalvageKingGameType vtable (from `loc_5612F0`) |
| `dword_6BDA0C` / `dword_6BD12C` / `dword_71316C` | DM/CTF/SK class **descriptors** (NOT the active instance) |
| `stats + 0x14` | team id; 0=Blue, 1=Red; 0..N-1 for N-team modes |

## Working style for this project

- Prefer **runtime dumps** (`zax_dump.bin`) over more static analysis — runtime
  data has resolved every blocker so far.
- When the patch crashes, ask for the Wine log; faulting EIP + fault address
  usually points straight at the bug.
- Long-form plans live in `~/.claude/plans/`; keep them concise and reference
  `docs/` instead of duplicating.
- The user is fluent in this codebase. Match their level — don't over-explain
  background they already wrote down in `docs/`.
