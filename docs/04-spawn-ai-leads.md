# 04 — Player participants, spawning & AI (Phase 2 map)

Target fidelity: a **full player-bot** (participant + character + team color + scoreboard,
AI-driven). This file is the architecture map. Items are tagged **VERIFIED** (read in IDA /
confirmed by behavior) or **LEAD** (plausible, confirm before coding).

## The good news: the engine already has player-driving AI  (VERIFIED — by class names)
Registered classes exist specifically to control a player character:
- `CPlayerControlAI` @ `0x61E7DC` — class descriptor `dword_7110EC`; has a **"Player Num"
  property at offset +28** (binds the AI to a participant slot). Registration trio:
  `sub_542170` / `sub_5421C0` / `sub_542220`.
- `CPlayerWalkingControlAI` @ `0x61E94C`, `CPlayerWalkingWeaponAI` @ `0x61EA74`,
  `CPlayerWalkingItemAI` @ `0x61EA8C`, `CPlayerWalkingControlSettings` @ `0x61E7FC`.
These are the building blocks of a bot brain (move / aim+shoot / use items). They are
**instantiated data-drivenly via the reflection factory** (see below), not by direct calls —
`dword_7110EC` is only referenced by its own registration code.

## Object model (VERIFIED)
- **World/entity manager** = `dword_6C2080` (huge xref count). Exposes typed sublists via
  vtable slots, e.g. **`dword_6C2080->vtbl[176]()` = the player list**; `vtbl[4]()` =
  another sublist + a per-frame sync (called as `(...)->vtbl[4](player)` to register/sync a
  player). Player-list iteration: `list->vtbl[44]()` = count, `list->vtbl[52](i)` = player i.
  (`sub_59B260` and `sub_51C310` both use this exact pattern.)
- **Player container classes**: `CPlayerListHolder` @ `0x618068`, `CCharacterListHolder`
  @ `0x618050` (class desc `dword_6C28EC`). Ctor/registration `sub_4EF720`. Reflection
  registers a "Player" sub-record with fields at offsets {16,20,12} (size 4 each).
- **Player → stats object** = `sub_51F440(player)` (wrapped by `sub_5BA820(player)`, which
  first calls `dword_6C2080->vtbl[4]()` to sync). On the stats object:
  - **`+0x14` = team id**, values 0/1 → **Blue/Red** (toggled in the team-change handler
    `sub_51D240`: `t = *(stats+0x14); *(stats+0x14) = (t==0)`).
  - `+0x16` (`int16` at +246 of an inner object in `sub_51D240`) — another per-player field.
  - Frags/score and name also live on/near this object (offsets TODO — read the scoreboard).
- **Match state** = `CMultiPlayerGameData` at `*(level + 0x30)`, `level = mgr->vtbl[0x184]()`,
  `mgr = dword_713F14`. Fields: `+8` Match-Is-Over (byte), `+0xC` "players ready" (dword),
  `+0x10` **double** match-timer accumulator. (This object is match state, **not** the player
  list.) Reflection desc `dword_6D001C`; type-check via `sub_416790(dword_6D001C)`.

## Reflection / factory (VERIFIED, the key to creating objects by class)
Classes register with:
`sub_415780(&class_desc_global, "ClassName", f, parent, fieldSize, flags, ctor, dtor, propReg)`.
Per-class property registration uses `sub_54D6E0` / `sub_54DDF0` / `sub_419230`
(`(name, off, size, off, …)`). Instances are created by name through this system, which is
how a level's entity data attaches components like `CPlayerControlAI`. **This is the intended
mechanism to instantiate a bot's AI component and bind its "Player Num".**

## The spawn path (VERIFIED — server side, on "client ready to spawn")
Handler: **`sub_5AC230`** (a method in the `CClientReadyToSpawnBlockReader` vtable
`off_603FB8`; the reader global is `dword_714058`). Core sequence:
```
list   = dword_6C2080->vtbl[176](conn…)        ; player list
player = list->vtbl[112]()                      ; the participant/connection's player
state  = list->vtbl[116]()
if (*(BYTE*)(state+40)) {                        ; CD-key/validation gate
    mgr->vtbl[452]()                             ; manager pre-spawn hook
    sp = sub_4F5D60(player)
    sp->vtbl[132]()                              ; <-- SPAWN the character (vtbl[0x84])
    sub_4E77E0(state)
    dword_6C2080->vtbl[4](player)                ; register/sync the player
    broadcast "<name> …" via sub_59B260(msg,-1)  ; join message (template unk_71407C)
} else  /* protocol breach: "Failed to respond to CD Key challenge." */
```
So **`vtbl[132]` (0x84) on the spawnable is the character-spawn method**, and
`dword_6C2080->vtbl[4](player)` registers the player. This path is bound to a **network
connection object**, which a bot does not have.

## Connection-free spawn primitive (VERIFIED)
**`sub_51D1A0(playerObject)`** spawns a given player's character with **no network
connection** required — it is a game-type virtual ("spawn this player", present in several
class vtables: data xrefs `0x5EF5FC`, `0x5F0E0C`, `0x5FB218`, `0x5FEE00`). Body:
```
idx = sub_5BACE0(player)          ; validate class + return player index (sub_4EF9E0); -1 if bad
if (idx != -1) sub_4EF900(idx, 0)
sub_4FBF30(player)
if (idx != -1) {
    mgr->vtbl[452](mgr, sub_4F1050(mgr), idx, 0, 0)   ; pre-spawn hook
    sp = sub_4F5D60(player)
    sp->vtbl[132]()                                    ; spawn character
}
```
`sub_5BACE0(player)` = validate (`sub_416790`) + `sub_4EF9E0(player)` → player index.
So once we have a participant **player object**, `sub_51D1A0(player)` spawns it. This is the
spawn call a bot should reuse.

## A player IS a `CEntityCharacter`; the list is event-driven (VERIFIED)
- The player/participant object is a **`CEntityCharacter`** (class desc `dword_6BDEA0`,
  **size 300 / 0x12C bytes**; reflection registration `sub_49AA00`, allocator factory
  `sub_4903F0`). `sub_5BACE0` validated its arg against this class; `sub_51F440(char)` returns
  the per-player **stats/info** sub-object (team at `+0x14`, etc.).
- Some `CEntityCharacter` own fields (offsets, from `sub_49AA00`): `+28` Weapons-Use-Ammo,
  `+264` Inventory, `+268/272` Home Pos X/Y, `+276/280` LastDischarge Pos X/Y,
  `+292` LastDischargeSequenceName. (Name/frags/team live on the `sub_51F440` stats object,
  offsets still TODO — read the scoreboard renderer.)
- **`CPlayerListHolder` membership is event-driven, not an explicit "add" call.** The holder
  observes character lifecycle: `sub_4EF840` (a holder vtable method) **removes** a character
  from its array when the character fires a "Delete" event. Symmetrically, creating a
  `CEntityCharacter` registers it into the appropriate holder automatically. Holder layout
  (`sub_4EF440`): `+0` vtbl `off_5F8EF0`, `+8` vtbl2, `+12` array of `CEntityCharacter*`,
  `+16` count, `+20` capacity.

### Resolution of "the add-participant gap"
There is **no simple AddParticipant() to call** — adding a participant = **creating a
`CEntityCharacter` player entity** (which self-registers), then spawning it with
`sub_51D1A0(char)` and binding AI. So Phase 2 implementation centers on *creating a player
character entity at runtime*.

## CORRECTION from runtime probing (experiments 1–2) — the object model is layered
Live probing (hook reads from a real host MP match) revised the model:
- The list from `dword_6C2080->vtbl[176]()` has **count 1** when hosting alone, element 0 =
  the host. **`*host == 0x5FB580`** — but that vtable is a **message-sink / connection
  sub-object** (~52 B, ctor `sub_51FE60`, embeds a 16-B list at +40), **not** a
  `CEntityCharacter`. Its `+0x5C`/`+0x84` slots are generic refcount stubs
  (`sub_417420`/`sub_4167B0`), so "clone at 0x5C" does **not** apply here.
- That sub-object is part of a larger **player object** (vtable `off_5F91C8`, ctor
  `sub_4F7C50`) which embeds two `0x5FB580` sinks (at +60, +112) and, on construction,
  registers itself with the world manager (`dword_6C2080->vtbl[424](self_a, self_b)` and
  `vtbl[164](connId)` stored at +58).
- The outermost **participant** object is **280 bytes** (vtable `off_604DE4`), created by
  factory **`sub_5BA790(connId)`** (`new 0x118` → `sub_4F7C50` → extra init); destroyed by
  `sub_5BA150`. `sub_5BA790` is reached only as a **factory slot** (vtable `0x602FE0`,
  slot 6 = `0x602FF8`), i.e. instances are minted by the reflection/connection system by
  class, not by any direct `call` — so the *creation entry point* (who invokes the factory
  when the host's own participant is born) is **still not located**; it is behind the
  reflection factory + connection-accept code.

So: **a participant is a layered object (participant 280B → player sub-obj `off_5F91C8` →
message-sink sub-objs `0x5FB580`), and the in-world `CEntityCharacter` is linked separately
at spawn.** Synthesizing one by hand is involved; the clean path is to drive the host's own
participant-creation call once found (it auto-registers with `dword_6C2080`). `sub_51D1A0`
still spawns a participant's character once we have one.

## Recommended Phase 2 implementation approach (original; superseded by the correction above for the clone step)
**Clone the existing host player** rather than build a `CEntityCharacter` from scratch:
1. Get the host's player char from the list: `list = dword_6C2080->vtbl[176](); host = list->vtbl[52](0)`.
2. Clone it via the entity clone vtable slot (`vtbl[0x5C]`, seen in `sub_4D05A0`) → a new
   fully-formed `CEntityCharacter` of the same class (auto-registers into the list).
3. Set the clone's team (`sub_51F440(clone)+0x14` = 0 Blue / 1 Red; prefer a real "set team"
   call if found) and a name.
4. Spawn it in-world: `sub_51D1A0(clone)` (→ `sub_4F5D60(clone)->vtbl[132]()`), optionally at
   a team spawn point.
5. Attach a player brain: factory-create `CPlayerControlAI` (+ `CPlayerWalking*AI`), set its
   "Player Num" (+28) to the clone's index (`sub_4EF9E0(list, clone)`), attach to the clone's
   component list so the entity update ticks it.

This is necessarily **empirical from here** — exact clone slot, component-attach call, and
stats offsets must be confirmed by running the game and observing (the user runs & reports).

## Runtime-verified participant & list layout (experiments 1-7, via zax_dump.bin)
A reusable **file-dump tool** in the hook (CreateFileA/WriteFile/CloseHandle on the render
thread — works fine under Wine, so the original crash was NOT file I/O) appends raw memory
to `zax_dump.bin` (chunks = 8-byte LE header [srcVA,len] + bytes). Findings:

- **The player list** = `(*dword_6C2080)->vtbl[0xB0]()` (NOT `mgr+0x30`; that is a CFont
  array, vtable 0x5f437c = "CFont"). The list object (vtable `0x602fa8`): `+0x04` = element
  array ptr, **`+0x2C` = count** (=1 with only the host). `vtbl[0x2C]`=count, `vtbl[0x34](i)`=get(i).
  List ctor `sub_59BD50`; list used by the 5.4KB server core **`sub_59E730`**.
- **List elements** are participant **message-sinks** (vtable `0x5fb580`) located at
  **participant + 0x3C**. So `participant = get(i) - 0x3C`.
- **Participant** object (vtable `0x604de4` primary / `0x604df4` secondary @ +0xFC; 0x118=280 B,
  factory `sub_5BA790`):
  - `+0x08` = **connId, = -1 for the local host** (our factory(0) orphan had the wrong id).
  - `+0x3C`, `+0x70` = the two sinks (vtable `0x5fb580`).
  - `+0xC0..+0xD8` = floats (world position / bounds, e.g. ~510, ~337, ~845).
  - `+0xEC` = 0x64 (100; likely health). `+0xAC/+0xB0` = 80/50 (possible score fields).
  - name/team/character pointers are among `+0x1C/+0x24/+0x4C/+0x64/+0x98/+0xB8/+0xBC/+0xE8/+0x100`
    (follow-pointer dumps still needed to pin each).
- **Manager note:** `*dword_6C2080`'s vtable differed across sessions (0x6020C8 vs 0x602dc8) —
  it is recreated per match/mode, so do NOT hardcode the manager vtable; always go through
  `[*dword_6C2080]+offset`. The `vtbl[0xB0]` list access is stable across sessions.

## The remaining blocker (precise)
`sub_5BA790` (factory) only **constructs** a participant; it does **not** add it to the list
(`sub_4F7C50`'s `vtbl[424]` register call is a no-op stub) and does not spawn a character.
So a bot needs the explicit **join/activate** path: add participant to the list (write
array `+0x04` / bump count `+0x2C`, or call the list's add method) and spawn its character
(`vtbl[132]` / `sub_51D1A0` once the participant has a CEntityCharacter). That join code lives
in the session/server core (`sub_59E730` and the session class whose vtable holds `sub_5BA790`
at `0x602FF8`). NEXT: locate the host's own local-participant creation (connId -1) at
host-start and drive that, since it does the full add+character+team setup connection-free.

## Session / manager identities (verified)
- **`dword_713F14`** = the game **manager** (vtable `0x602dc8`); ctor `sub_59A670` (registers
  cheat commands "medic"/"giveall"/"cash"/…, sets `dword_713F14=this`, clears the session);
  dtor `sub_59AA10`. Our MP gate `mgr->vtbl[0x184]` is on this.
- **`dword_713F18`** = the **session / participant container** (vtable `0x602fa8` primary,
  `0x602FE0` secondary). Lazily built by `sub_59BD50` (= manager `vtbl[0x1AC]`, a match-start
  "set up N players" loop). `mgr->vtbl[0xB0]()` returns this session.
  - primary vtable: `vtbl[0x2C]`=count=`sub_4F7EF0`, `vtbl[0x34]`=get=`sub_4F7F90`,
    `vtbl[0x24]`=`sub_5BA9D0` = "on participant added" (broadcasts join via `sub_59B260`,
    registers sink at participant+0x3C, notifies game-type via `sub_59FF90(mgr)->vtbl[112]`).
  - secondary vtable `0x602FE0`: `createParticipant`=`sub_5BA790` (slot 6 @ `0x602FF8`),
    plus GameSpy query fillers (`sub_5BAD10` mapname/gametype, `sub_5BADA0` gamename=zax).
- **Every key step is reached via vtable dispatch** (`sub_5BA790`, `sub_5BA9D0`, `sub_59BD50`
  all have a single data xref = a vtable slot), so the host-start creation can't be pinned by
  static call-graph alone. The match-start flow lives in `sub_59E730` (5.4KB server core) and
  the menu/host code, dispatched through the manager/session vtables.

## The join handler (found via runtime detour of the factory — experiment 9)
Detouring `sub_5BA790` to record its caller showed: factory always called with `connId=0`
(4 calls for a host+1 session), caller = **`sub_480800`** = the **DirectPlay player
add/remove handler**. Recipe per added player:
```
p = session->vtbl[0x50](0, ...)     ; create the 280B participant (the factory)
registrar(mgr+16)->vtbl[8](&p)      ; register it
p[+8]  = DirectPlay player ID       ; host = -1, remote = real id (NOT the factory arg)
p[+12] = index ; p[+6] = 1          ; mark active
... DirectPlay GetPlayerData -> name (WideCharToMultiByte), else random a-z[10] ...
```
- `mgr` (ecx, the DirectPlay session manager) has: session @ `+4`, registrar @ `+16`,
  participant-array @ `+20`/count `+24`, a pending add/remove queue @ `+1101` (stride 12,
  up to 100 slots), a "changed" flag @ `+2300`.
- **All participants (host + clients) are born from DirectPlay player-join events.**
  Character spawning is separate (ready-to-spawn net msg -> `sub_5AC230` -> `vtbl[132]`).

### BETTER: a DirectPlay-FREE local-participant template (`sub_480530`)
`sub_480530` (a DP-manager method, vtable `0x5f15xx`) creates the **local host participant
without any DirectPlay name query**:
```
p = session->vtbl[0x50](1)        ; session = *(mgr+4) (== dword_713F18); create participant
registrar->vtbl[8](&p)            ; registrar = *(mgr+16); insert into the list
p[+8]  = -1                       ; local id (host). A bot can use a different local id (e.g. -2)
p[+12] = 0
p[+5]  = 1 ; p[+6] = 0            ; active flags
mgr[+57] = 1
```
This is the recipe to replicate for a bot — **no DirectPlay simulation needed**. It does
NOT set a name or spawn a character (those are separate). The DP-manager methods:
`sub_480530` = create-local-participant/teardown, `sub_480BD0` = per-frame poll
(both vtable slots @ `0x5f15d0`/`0x5f15e8`).

Remaining inputs/steps for the bot:
1. Obtain the **DP-manager instance** (ecx of `sub_480BD0`/`sub_480530`) — capture at runtime
   by detouring the per-frame poll `sub_480BD0` (store ecx in .zaxbot), or find its global.
   From it: session = `*(mgr+4)`, registrar = `*(mgr+16)`.
2. Replicate the create+register+set-fields above (use a unique local id, e.g. -2).
3. Give it a name and a team (team field via `sub_51F440(p)+0x14`).
4. Spawn its character (the ready-to-spawn path `sub_5AC230` / `vtbl[132]`).
5. Attach `CPlayerControlAI` (+ `CPlayerWalking*AI`), bind its player number (participant +0x104),
   ensure it ticks.

### Implication for a host-only bot (DirectPlay-injection alternative — now NOT needed)
A faithful scoreboard bot must EITHER (a) inject a fake entry into the DirectPlay pending
queue (`mgr+1101`, set added flag + a fake player id, set `mgr+2300`) so `sub_480800`
creates+names it, then drive the spawn directly (skip the net ready-msg) and attach AI; OR
(b) replicate `sub_480800`'s create+register+activate steps by hand for one participant, then
spawn + AI. Both require the DirectPlay manager `mgr` instance and several correct calls, plus
a separate character-spawn step and per-frame AI — i.e. the bot must simulate join + spawn +
input. This is a large, multi-step build with crash risk at each step.

## ✅ MILESTONE 1 (WORKS in-game): create a scoreboard participant
Replicating `sub_480530`'s local create, on the message-pump thread, under the DP lock:
```
mgr = captured DP manager (detour sub_480BD0 prologue: mov [cap_mgr],ecx; runs on DP thread)
EnterCriticalSection(0x6BDBF0)              ; lpCriticalSection_  (serialize w/ DP thread)
  p = sub_5BA790(1)                          ; create 280B participant (factory)
  ecx = mgr+0x10 (registrar) ; edx = [ecx] (vtable 0x5fb558)
  call [edx+8]  (= sub_564F20, array push)   ; insert &p   *** opcode FF 52 08, not FF 51 08 ***
  p[+8] = -2 ; p[+0xC] = 0 ; p[+5] = 1 ; p[+6] = 0
  mgr[+57] = 1
LeaveCriticalSection(0x6BDBF0)
```
Result: bot appears on the scoreboard, game stable. (Diagnosed the earlier crashes with
on-screen markers + a registrar dump + the Wine `+seh` log, which showed a jump to EIP=1 =
the wrong modrm `call [ecx+8]`.) Lesson: hand-encoded modrm bytes are the main hazard — the
register's `this` is in ECX, the vtable in EDX; the call must be `FF 52 08`.

## MILESTONE 2 (next): spawn the bot's CEntityCharacter
The participant has no character (can't be found in the arena). Character spawn happens in
the ready-to-spawn handler `sub_5AC230`: `v11 = sub_4F5D60(player); v11->vtbl[132]()`
(`vtbl[0x84]` = spawn). NOTE corrections: `sub_59FF90` is a name-cache (NOT the game-type);
`sub_4F5D60(obj,idx)` = `obj`'s `array[idx]` (array @ obj+0x290, count @ obj+0x294).
The bot participant also still lacks its player-number (host=1/remote=2 at participant+0x104)
and position/sub-objects. Plan: find the connection-free "spawn this participant's character
at a spawn point" path (round-start/respawn analog of `sub_5AC230`) and invoke it for the bot.

## ★ The per-player character+control setup: `sub_59DF90` (= mgr `vtbl[0x1C4]`)
Called per player index at match-start (`sub_59BD50` loop) and at pre-spawn (`sub_5AC230`).
`sub_59DF90(this=mgr, a2, a3=playerIndex, name, a5)`:
- `v7 = mgr->vtbl[0x80](index)` — **creates the player's CEntityCharacter**.
- names it "%s's Player" from `sub_51F440(player, index)` stats.
- `mgr->vtbl[0x9C](v7, index)`; `sub_4EF900(mgr+161, index, v7)` — **stores the character into
  `mgr->array[0x290][index]`** (the per-player "spawnable" slot read by `sub_4F5D60`).
- `ctrl = sub_542170(0)` (CPlayerControlAI factory); `v7->vtbl[0x100](ctrl,…)` — **attaches the
  player-control component** to the character; `ctrl->vtbl[172]/[176]`.
- `sub_4F4950(v7, a2, name, a5)`.
So this single call does character-create + register-as-spawnable + control-attach (likely
milestones 2 AND 3). World placement is the separate `mgr->array[index]->vtbl[0x84]` spawn
(`sub_5AC230`) and/or `sub_4F4950`.

PLAN (same proven pattern as the participant): **detour `sub_59DF90` to capture its real args**
(a2, playerIndex, name, a5) when the host's character is created at match-start; then replicate
`sub_59DF90(mgr, a2, botIndex, name, a5)` for the bot, followed by the `vtbl[0x84]` world spawn.
Need the bot's player index/number (participant +0x104; host=1, remote=2) consistent with the
index used here.

## sub_59DF90 = full character create + place (captured args, exp 12)
Host's call (captured by detouring sub_59DF90): `this=dword_713F14 (0x1910c08), a2=<heap ctx>,
index=0, name=NULL, a5=NULL`, callcount=1. `a2` is a SHARED per-match context (sub_59BD50
passes the same a2 for every player in its setup loop), so it can be captured once and reused.
`sub_59DF90` ends in `sub_4F4950 -> sub_4F53F0` = relocate-to-spawn-point, so it CREATES +
NAMES + ATTACHES CONTROL + PLACES the character. So milestone 2 ≈ one call:
`sub_59DF90(dword_713F14, capturedA2, botIndex, 0, 0)`.

### Milestone-2 build plan & risk
Combined hook on B: (1) create+register participant (milestone 1, needs DP mgr from sub_480BD0
detour) -> bot at DP-registrar index; (2) `sub_59DF90([dword_713F14], capturedA2, botIndex,0,0)`
(a2 from sub_59DF90 detour). Needs TWO detours (DP mgr + a2). RISK: index alignment — the DP
registrar index vs the game-manager char/stats index (mgr+0x290 / sub_51F440) may differ; if
the game-manager side has no player at botIndex, `sub_59DF90` (sub_51F440 / vtbl[0x80]) may
fault. Use step markers to localize. botIndex = DP registrar count before insert (host=0 -> bot=1).

## ✅ MILESTONE 2 (WORKS in-game): bot character spawns & is visible
On B: create+register participant (milestone 1) under the DP CS, then
`sub_59DF90([dword_713F14], capturedA2, botIndex, 0, 0)` (botIndex = DP-registrar count
before insert; set participant +0x104 = botIndex+1). Result: a 2nd named player spawns at a
spawn point, visible, on the scoreboard, stable. `a2` captured by detouring `sub_59DF90`;
DP manager captured by detouring `sub_480BD0`.

### MILESTONE 3 (remaining): make it an AI bot, not an input-mirrored clone
Observed in-game after milestone 2:
- the bot **mirrors the host's input** (both move/shoot together) and the **camera switched to
  the bot** — because `sub_59DF90` attaches a `CPlayerControlAI` and the bot is a *local* player
  (connId -1-like), so the engine drives all local controls with the same input & follows the
  newest local player.
- bot uses the host's skin/appearance (cloned defaults).
- pressing B a 2nd time crashes: `sub_4FC200` this≈null reading `[0x14]` — out-of-range index 2
  in `sub_59DF90`'s `sub_4EF900(mgr+161, idx, char)`. So cap to one bot / fix the index for now.

Milestone-3 investigation: how `CPlayerControlAI` (ctor/vtable via `sub_4FD220`, "Player Num"
@+28) decides input-vs-AI; the "controlled/local player" + camera-target global (so the host
keeps control). Likely fix: mark the bot non-local (connId != -1) and/or give it the AI brain
(`CPlayerWalking*AI`) instead of the input-reading control, and don't switch the camera target.

## Milestone 3 reality (input/control/AI is deep)
- `sub_59DF90` -> `mgr->vtbl[0x9C] = sub_59BE20(char, index)` creates a **288-byte
  `CPlayerWalkingControlAI`** (`sub_543190`) and binds its Player Num to `index`
  (`sub_542550`). This is the **movement controller**. Both host and bot get one.
- The bot **mirrors** because both walking controllers read the **same global keyboard
  action-state**; the bot's isn't switched to network/AI input.
- **connId is NOT the local/controlled determinant** (tested: positive connId 0x424F54 still
  mirrors + steals camera). So the input-source (keyboard vs network vs AI) and the
  camera/controlled-player are decided elsewhere — in `CPlayerWalkingControlAI`'s think and a
  controlled-player/view mechanism, both vtable-dispatched and buried.
- Secondary crashes from incomplete bot setup: 2nd bot = `sub_4FC200` null (index 2);
  end-game = `sub_5BA150` (participant dtor) null `[+4]` deref.
- To finish milestone 3: RE `CPlayerWalkingControlAI` think (input-source branch) so the bot
  runs AI not keyboard; find/reset the controlled-player/view global so the host keeps camera;
  cap to one bot / make the participant dtor-safe. This is substantial further RE.

## ★ KEY FINDING: there is NO built-in player-bot AI
`CPlayerWalkingControlAI`'s think (vtable `off_5FCF18`) is pure HUMAN input:
- movement: `sub_543B60` computes direction from the GLOBAL cursor (`*(dword_6C2080+680)`),
  gated by controller flag `+279`; both the host's and the bot's controllers read the SAME
  cursor -> movement mirrors. (Zax is cursor-driven: the player walks toward the cursor.)
- firing: `sub_543830` reads the fire button + fires the "Primary" weapon.
There is no network/AI direction source in this controller — the "AI" in the class names is
the component framework (`CAIBase`), not bot intelligence. Real AI in the game is on MONSTER
entities (`CAttackAI`/`CApproachTargetAI`), not player characters.

Implication: a *fighting player-bot* requires WRITING the AI ourselves (each frame: pick a
target, compute a movement direction toward it, trigger fire) AND solving per-controller input
injection (the cursor/fire input is GLOBAL, shared with the host) — a large, custom effort.
The more viable route to "an opponent that fights" is to spawn a MONSTER entity with the
existing `CAttackAI`/`CApproachTargetAI` (proven single-player AI), optionally paired with the
scoreboard participant we already create.

## Stage A status: monster AI attaches but does not drive
Read the bot char from `*(*(mgr+0x290)+4*botidx)` (sub_59DF90 stores it there but doesn't bump
the count, so `sub_4F5D60` returns null). `char->vtbl[0x100](CApproachTargetAI_desc)` attaches
the component cleanly (no crash, marker G), but the bot's behavior is unchanged because:
1. the component is NOT initialized — `sub_59DF90` does `comp->vtbl[172](0,char); comp->vtbl[176]()`
   after attaching the human control; my attach skipped that.
2. the **human controller (`CPlayerWalkingControlAI`) is still attached and dominates** (reads
   the cursor -> mirroring). HUD shows host stats while camera follows the bot -> the local-player
   state is split between them.
Even after init, `CApproachTargetAI` needs a **target** and a **"Move Behavior"** (+16) to do
anything; monsters get these from level data. So Stage A needs: remove/disable the human
controller, init the monster AI, set a Move Behavior + target — and it's still UNPROVEN that
monster AI can drive a player `CEntityCharacter` at all (architectural mismatch: player bodies
are built for human input; AI lives on monster bodies). This is a deep, uncertain effort.

## ✅ M3 progress: CLEAN passive bot (no mirror, no camera steal)
Skipping the human walking controller fixes everything mirror/camera-related: a "bot-mode"
flag is set around the `sub_59DF90` call; a detour on `sub_59BE20` (the controller creator)
does `xor eax,eax; ret 8` when bot-mode is set, so the bot gets NO human controller. Result
in-game: bot spawns, on scoreboard, visible, **no movement/firing mirror, no camera steal,
host fully functional, stable** (1st bot). So the camera/mirror were all caused by the human
walking controller. CApproachTargetAI is attached (vtbl[0x100]) but INERT — not initialized,
no Move Behavior (+16) or target. Next: drive it (init + Move Behavior + target) — still the
deep/uncertain part (monster AI steering a player body is unproven). Crash guards still needed:
2nd bot (index 2: `sub_4FC200` null) and end-game (`sub_5BA150` participant dtor null).

## CApproachTargetAI think (sub_42E5D0 = vtbl[0x64]) — what it needs to drive the bot
`sub_42E5D0(this=AI, char, dt)`:
- `target = char->vtbl[0x118]()` — reads the CHARACTER's current target. If null -> does nothing.
  So the bot's char target must be SET (to the host). (For monsters this is set by aggro.)
- if target valid: compute dist; if > StopDistance(AI+204), compute a point toward target and
  `sub_4303F0(char, pos, 1.0, 0)` (set move destination), then `(AI+16)->vtbl[100](dt, char)` —
  the **"Move Behavior"** at AI+16 does the actual navigation/move. If AI+16 is null -> no move/crash.
So to make the bot approach+attack: (1) set the char's target (find the char "set target" method,
the setter paired with vtbl[0x118]); (2) give the AI a valid Move Behavior (AI+16) — the
navigation sub-object monsters get from level data. The Move Behavior (pathfinding) is the deep
crux, and it's the SAME navigation capability Stage C (objective routing to bases) will need.

## Deferred polish (after core AI navigation)
- **End-game + 2nd-bot crashes: FIXED.** End-game was our bad `+0x104` write (it's the
  participant's owned-entity COUNT read by dtor `sub_5BA150`; `sub_5BA790` inits it 0 — leave it).
  Multi-bot indexing now uses 16 scratch slots, unique synthetic ids `0xBADC0DE0..0xBADC0DEF`,
  `dpmgr+0x18` for current participants, and `[[dpmgr+0x08]+0x0C]` for the hosted
  session's advertised `maxplayers` value (same source `sub_47EE70` serializes next to
  `numplayers`). `level+0x58` was a bad cap source for the active object returned by
  `mgr->vtbl[0x184]()`; on a max-3 map it let the hook enter the synthetic DP queue path
  at full count and crash before the `D` marker. `mgr+0x298` also is not the map cap:
  older dumps show it tracking the live char-array count/capacity (`1`, `2`, `3`).
  The hook checks the count once before slot selection and again inside the DP critical
  section before publishing the synthetic queue entry. The target `mgr+0x290[botidx]`
  slot is zeroed before `sub_59DF90`, and `mgr+0x294` is bumped to at least `botidx+1`.
  Random crashes ending at step `AM2BC` were in `sub_480800` after the participant was
  created but before it returned: the DirectPlay name block at `0x480889..0x480993`
  queries DP player data for our synthetic id. Since no real DP player exists for that id,
  the size query can leave `count=0`, then the block writes a 24-byte header into a 0-byte
  allocation and later derefs the stack-context name object. Synthetic ids now jump over
  that block; real player joins still run it. Important implementation detail: the skip
  must `add esp,4` for the already-pushed DP flags arg and restore `edi` from `[esp+0x1C]`
  before resuming at `0x480993`.
  Important fix: after the synchronous
  `sub_480800` call consumes our synthetic DP queue entry, the hook clears the queue slot on
  success; leaving stale "added" flags lets later polls/spawns duplicate participants and can
  push the live count past the cap.
- **Bot hurt-sound on host: RESOLVED (via mute-scope around `sub_48D380`).** Diagnosed with a
  ring-buffer event recorder (32 slots × 40 B; gated on bot-spawn) that captured tag + source +
  `sub_4FC6F0(source)` root + `sub_4F5D80(worldmgr)` "local listener" per call. Findings:
  (a) the local-listener hypothesis is false — `sub_4F5D80` returns `charArray[0]` = the host
  char unconditionally (`s4f5d80_hits` stayed 0). (b) `detour_48D5A7` correctly skips the
  `[model+0x58]; sub_4FC8A0` pain-sound site for the bot, but `sub_48D380` continues past
  `0x48D5B2` into `call [eax+0x16C]` (worldmgr hit-broadcast, `0x48D667`) and
  `call [eax+0x184]` (damaged entity virtual, `0x48D690`). At least one of those emits a sound
  (id 0xF8 in the test capture) from a non-bot-rooted effect emitter — `sub_4FC6F0` returns
  the emitter itself with no parent link to bot, so source-root filtering cannot catch it.
  Fix in `zax_patch.py` (`detour_48D380`): when `ecx == botchar`, the detour swaps the
  caller's return address on the stack with `bot_damage_trampoline`'s VA (the original is
  saved in `orig_caller_ra_va`), sets `bot_sound_mute = 1`, then runs `sub_48D380` to natural
  completion. The existing `detour_4EA880` / `detour_4EAA60` mute filters silence every sound
  dispatched within that scope. When `retn 0Ch` fires, control lands in the trampoline, which
  clears the mute and forwards to the saved caller RA. Result mirrors second-PC behavior: bot
  takes damage normally (can be killed, score updates, AI hooks run) but the host hears no
  local-channel hurt SFX. Single-threaded codebase + no recursion in the damage handler =>
  one `orig_caller_ra_va` slot is sufficient.
- **Bot doesn't respawn after death** — bot can now die (damage applies). Respawn cycle still
  needs to be wired in (the engine respawn path is connection-bound; a host-local respawn for
  the bot would reuse the same `sub_59DF90` recipe that spawn does).
- **Multi-bot** needs the game-manager char array (mgr+0x290) / index growth (index 2 = null now).
- **Appearance** inherits host skin; team color (B=Blue/R=Red) not yet applied.
These are tracked for a polish pass; the core remaining work is AI navigation (Stage A) + objectives.

## ★ STAGE A BREAKTHROUGH: monster AI drives a player character
The CORRECT attach recipe (vtbl[0x100] is only a FINDER, not an adder):
1. `comp = sub_42E150()` — allocates+inits a `CApproachTargetAI` (vtable 0x5EE868; its
   "Move Behavior" is an EMBEDDED sub-object at comp+0x10, inited by `sub_4302C0(comp+0x10)`).
2. `botchar+0x1C |= 0x800000` — the AI-control flag.
3. `sub_4FBC50(this=botchar, comp)` — attach the component (ret 4). Char component count
   (char+0x40) went 1 -> 2, confirming the attach.
4. target = `botchar+0x14` (the AI reads it via vtbl[0x118]=`*(char+0x14)`).
Result in-game: the bot **spawned, tried to MOVE (AI engaged!), then disappeared after ~0.5s**
(no crash). So monster AI CAN steer a player body — core Stage-A feasibility proven. Open: the
AI-active bot vanishes ~0.5s in (the passive bot was stable) — likely walks to its death or its
char isn't a complete combatant (lacks fields the host char has: floats @+0xb8/+0xc0,
+0x114/+0x118, and an embedded sub-object @+0x130 — possibly health/weapon/body). Next: localize
movement-vs-activation, then complete the char (health/body) so it survives.

## Stage A mismatch: player char needs its controller for animation/survivable movement
Localized (in-game): with the monster AI attached + the `0x800000` flag but NO target, the bot
**survives & stands still** — but has **no idle animation**. With a target, it **moves then dies
in ~0.5s**. So: (a) the player char's animation is driven by the human `CPlayerWalkingControlAI`
(which we skip), so the bot is animation-less; (b) the monster AI's movement (Move Behavior =
embedded sub-object at comp+0x10, vtable 0x5EEB68, exec = vtbl[0x64]) is not survivable for a
player body. FORK: (A) complete the player char so monster-AI movement is survivable + restore
animation (uncertain depth); (B) keep the player's own controller and feed it AI decisions
(target/aim/fire) — leverages the char's native animation/movement/fire, but needs per-bot
input/aim override (the cursor/fire input is global). B fits the player-char design better.

## Movement requires a nav move-component (key constraint)
`sub_4303F0(this=move-component, char, dest, speed, flag)` is the pathfinding move (nav state
@ this+128..+180, path query `sub_5386B0`). It is NOT a char method — you need a nav-capable
MOVE COMPONENT to call it. The player char gets that from its **controller**
(`CPlayerWalkingControlAI`); the monster from its **Move Behavior**. So you can't move the bot
"directly" — movement is bundled with either the controller (which also steals the camera) or
the monster Move Behavior (which killed the bot). A fighting player-bot must resolve ONE of:
 - **B (controller):** restore `CPlayerWalkingControlAI` (native nav+animation+fire) + (i) keep
   the camera on the host (find the controlled-player/view global) + (ii) inject AI move-direction
   (detour `sub_542360`, identify the bot via controller+0x1C==index, override its output vector/
   angle toward the nearest enemy — needs FPU math) + (iii) AI fire.
 - **A (monster AI):** fix why the monster Move Behavior's movement kills the bot (nav mismatch
   on a player body) — cause unknown, possibly unfixable.
Either is a substantial, multi-cycle build. The clean spawning/scoreboard/no-mirror/no-camera
bot (current) is stable and is the foundation.

## Camera/view-target hunt (unresolved)
`dword_6C2938` = active/spawn player index (setter `sub_4F7E40(idx)`, getter `sub_4F7E50`,
`sub_4F7E60` = `this->vtbl[0x5C](dword_6C2938)`). It's the spawn-player index, not clearly the
camera target. The camera-steal is caused by the controller's existence (skipping `sub_59BE20`
fixes it), but the followed-player isn't an obvious single global to pin — it's entangled in the
controller/view system. `sub_4EB6F0` (via mgr vtbl[0x1A0]) removes a char from a list (uses the
same `sub_4FC200` that crashed on bot #2). Camera-pinning remains an open RE problem.

## Honest frontier assessment
Completing an autonomous fighting bot requires, on either path, deep & error-prone work:
 - movement is bundled with the controller (camera-steal, not yet pin-able) or the monster Move
   Behavior (kills the bot);
 - AI move-direction injection is screen-space + needs FPU math in hand-written x86;
 - plus target selection, respawn, and team-color polish.
This is a large, multi-cycle effort with real unknowns — at the practical edge of blind binary
patching. SOLID, DEMONSTRABLE RESULT achieved: a clean, stable bot that spawns at a spawn point,
shows on the frag scoreboard, doesn't mirror input or steal the camera, and is crash-safe
(map-capped multi-bot indexing + end-game guard). Monster AI was also proven able to move a
player char.

## Other still-open items
- Confirm Blue=0/Red=1 vs the team-hue code (`0x4690B0`) and find a clean "set team" call.
- Scoreboard field offsets (name/frags) via the scoreboard renderer (xref "Frags" `0x61C9F0`).
- Confirm `CPlayerControlAI` is ticked once attached (entity think/update dispatch).
1. **Host's LOCAL player-create path.** The host's own player (player num 0) is created
   locally, *not* via the network reader above. Find that path — it is the connection-free
   template to add a bot participant to `CPlayerListHolder`, give it a name + team, spawn its
   character (`sub_4F5D60`→`vtbl[132]`), and register it (`dword_6C2080->vtbl[4]`).
   Search: MP match-start / game-type start (around `CMultiPlayerGameType` `sub_51A320`,
   `CDeathMatchGameType` `sub_478EB0`) and callers of the player-list "add" method.
2. **Instantiate + bind a player AI.** Use the reflection factory to create a
   `CPlayerControlAI` (and the `CPlayerWalking*AI` set), set "Player Num" (+28) to the bot's
   slot, and attach it to the bot's character entity's component list. Confirm the per-frame
   entity update actually ticks this AI (find the entity think/update dispatch).
3. **Team assignment call.** Confirm Blue=0/Red=1 against the team-hue code (`0x4690B0`) and
   find the proper "set team" call (vs. directly writing stats+0x14) so the scoreboard color
   and team logic stay consistent. For deathmatch, "Is Team Game" is false → team is moot
   (random/none).
4. **Scoreboard fields.** Read the scoreboard renderer (xref "Frags" `0x61C9F0`, "Kills"
   `0x61CA00`) to confirm name/frags offsets so the bot shows correctly.

## Risk note
The connection-bound spawn path means a bot must either reuse the host's local-player path
(preferred) or fabricate a minimal connection-like object (fragile). Resolve item (1) before
committing to an approach.
