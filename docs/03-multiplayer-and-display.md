# 03 — Multiplayer state & on-screen messages

## The game/world manager — `dword_713F14`
A heavily-referenced global pointer to the main game/world manager (a polymorphic C++
object; `*dword_713F14` is its vtable). Several useful accessors hang off it.

### Reaching the live multiplayer data (VERIFIED at runtime)
```
mgr   = *dword_713F14
level = mgr->vtbl[0x184](mgr)     ; active level/match object
mpd   = *(level + 0x30)           ; CMultiPlayerGameData*, NULL outside an MP match
```
This chain is proven: the old logging build only reached its file-write after walking it,
and that write succeeded in an MP match. It doubles as the **"are we hosting an MP match"
gate** — `mpd != NULL` ⇔ in multiplayer. (`vtbl[0x184]` is a benign getter; calling it
outside a match returns NULL or a level with `+0x30 == NULL`, which the gate handles.)

Other relevant fields seen via the manager:
- `*(dword_713F14 + 736)` then `+56` — used by `sub_59B260` to reach the message subsystem.
- `dword_6C2080` — entity/player container; its `vtbl[176]` returns the active list, whose
  `vtbl[44]` = count and `vtbl[52]` = element(i). Used to iterate players when broadcasting.

## On-screen / system message — `sub_59B260`  (VERIFIED, used in Phase 1)
```
unsigned __stdcall sub_59B260(char* text, int type)
```
- With `type == -1` it posts a **system message to every player's on-screen message log**
  (the same path the engine uses for "%s changed teams" and kill messages).
- It lazily creates a `CZaxCustomRules` object (`dword_713F24`) and a per-player message
  queue on first use — normal main-thread allocations; safe to call from our hook.
- Requires being in a live match (it dereferences the manager + `dword_6C2080`), which our
  gate guarantees.
- Discovered by tracing the consumer of the "%s changed teams" localized-string object
  (`unk_6D0004`): `sub_51D240` formats it and calls `sub_59B260(formatted, -1)`.

### Localized strings
Score/UI text is stored as localizable string objects, registered at startup by tiny
`atexit`-style initializers calling `sub_501030(format, 0)` (e.g. `sub_51D210` registers
"%s changed teams" into `unk_6D0004`). To turn such an object into a `char*`, the engine
calls `sub_4E13A0(obj)`; formatting (printf-like) is `sub_4E09B0(dst_obj, fmt, args...)`.
For our own messages we just embed plain C-strings in `.zaxbot` and pass them to `sub_59B260`.

## Multiplayer classes & properties (string evidence; offsets mostly TODO)
Class-name strings (used as type identifiers):
- `CMultiPlayerGameData` @ `0x61BEF0` — methods cluster ~ `sub_519FF0`..`sub_51C800`.
- `CMultiPlayerGameType` @ `0x61BF38` — big ctor/prop-reg `sub_51A320` (0x1869 bytes).
- `CDeathMatchGameType`  @ `0x61052C` — `sub_478EB0` (0x6A2).
- `CTeamInfo`            @ `0x617550` — `sub_4E4640`, big `sub_4E5350` (0x1640).

Property-name strings (read from level/config; show the data model):
- `"Team/Is Team Game"` @ `0x61C810` — *"False for every man for himself (death match…),
  True if multiple players assigned to the same team (CTF, team death match)"*. The runtime
  flag distinguishing **deathmatch vs team** lives on the active game-type object (offset TODO).
- `"Team/Max Number of Teams"` @ `0x61C83C`, `"Team/Team Number"` @ `~0x6178A8`,
  `"Game Type/Team Number"` @ `0x618AA0`.
- `"Display/Red Team Hue"` @ `0x60F49C`, `"Display/Blue Team Hue"` @ `0x60F4B4`
  (referenced together @ `0x4690B0` — use this to confirm the Blue/Red integer ids).
- `"Frag Limit:"` @ `0x615C44`, `"fraglimit"` @ `0x61CA54`.
- Score msgs: `"%s killed %s"` @ `0x61C8A0`, `"%s killed TEAMMATE %s"` @ `0x61C8B0`,
  `"%s killed himself"` @ `0x61C878`, `"%s changed teams"` @ `0x61C864`.

### Candidate cached globals (LEAD — confirm before use)
A sub-agent suggested `dword_6D001C` (live `CMultiPlayerGameData`) and `dword_6D0020`
(active `CMultiPlayerGameType`, holding "Is Team Game") as cache pointers populated at match
start. **Unconfirmed** — verify in IDA. The proven manager chain above is the safe default.

## Open items for Phase 2
- Exact `CMultiPlayerGameData` layout: player/participant list (base, stride, count), and the
  per-participant fields (name ptr, team number, score/frags, entity ptr).
- The participant-creation/registration path (how the host's own player and a joining client
  are added) — the authoritative template to mimic for a bot.
- Blue/Red numeric team ids (from the team-hue code @ `0x4690B0` / `CTeamInfo`).
- The runtime "Is Team Game" flag offset.
