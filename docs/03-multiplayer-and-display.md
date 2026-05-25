# 03 - Multiplayer state and display anchors

## Manager chain

Primary verified globals:

| symbol | meaning |
|---:|---|
| `0x713F14` | game/world manager pointer (`dword_713F14`) |
| `0x6C2080` | world/entity manager pointer |
| `0x713F18` | session/participant container pointer |

Live MP data is reached with:

```c
mgr   = *dword_713F14;
level = mgr->vtbl[0x184](mgr);
mpd   = *(level + 0x30);       // CMultiPlayerGameData*, NULL outside MP
```

The hook uses this as the MP gate before showing menus, dumping snapshots, or
spawning.

## DirectPlay manager

`sub_480BD0` is the per-frame DirectPlay poll. `ecx` is the DP manager; the
detour stores it in scratch as `cap_dpmgr`.

Important fields used by the spawn path:

| field | meaning |
|---:|---|
| `dpmgr + 0x08` | hosted net-game descriptor |
| `*(dpmgr + 0x08) + 0x0C` | advertised `MaxPlayers` |
| `dpmgr + 0x14` | participant pointer array |
| `dpmgr + 0x18` | live participant count |
| `dpmgr + 0x38/0x39` | poll gates |
| `dpmgr + 0x44D` | pending add/remove queue cursor base; stride 12 |
| `dpmgr + 0x8FC` | changed flag |

The bot spawn code publishes a synthetic add entry under the DirectPlay critical
section at `0x6BDBF0`, calls `sub_480800`, then clears the queue entry so the
natural poll does not duplicate it.

## Participants and stats

`sub_480800` is the natural DirectPlay player add/remove handler. For synthetic
bot ids in `0xBADC0DE0..0xBADC0DEF`, it creates a normal participant through the
engine's path and writes the participant pointer back to the queue entry.

Useful participant helpers:

| symbol | meaning |
|---:|---|
| `sub_5BA790` (`0x5BA790`) | participant factory |
| `sub_5BA820` (`0x5BA820`) | participant/index -> stats object, with sync |
| `stats + 0x14` | team id |

Current team behavior:
- DM and SK are both free-for-all (SK gives every player their own collector
  base with a per-player color). Each bot gets `slot + 1` as a unique nonzero
  team value to avoid a same-team spawn-picker pathology.
- CTF is the only team mode. The dispatcher's digit '1'/'2' map to team `0`
  (Blue) / `1` (Red) — stored 0-indexed after subtracting `'1'`. Mode is
  resolved via `sub_59FF90(ecx=mgr)`, whose return's `[+0]` vtable matches
  `VT_DM_VA`/`VT_CTF_VA`/`VT_SK_VA`.

## Per-character appearance (colors)

A character's `color1`/`color2` floats live on a "player look" component
attached to the player char's **first child entity**, not to the player
char itself. The engine's `sub_5ABE80` (server-side handler for the
`CClientOptionsToServer` message) is the canonical apply path:

```c
target = (sub_4FC7C0(char) > 0) ? sub_4FC7D0(char, 0) : char;
appearance = sub_418790(ecx=dword_6C0520 /*class desc*/, push target);
if (appearance) {
    *(float*)(appearance + 0x0C) = (float)color1_int;
    *(float*)(appearance + 0x18) = (float)color2_int;
}
```

Calling `sub_418790` with the player char directly returns `NULL`; the
appearance lookup only resolves on the child entity. `sub_418790` is
`__thiscall` and pops its stack arg (`retn 4`). The same pattern is used
by `sub_46D450` (config-screen preview, against a preview avatar).

Color values are stored in the per-player config struct reached two ways
that point to the same memory:

- `*(participant + 0x1C)` from the participant pointer, or
- `*(stats + 0x1C)` from `sub_5BA820(idx)`.

Layout: `+0` name CString, `+4` color1 int, `+8` color2 int, `+0xC`
auto-switch byte. Sliders are 0..315 (`sub_4101F0(a1, 315, 1)` in
`sub_46D010`). The host's own config also lives at `dword_6BD2F8` —
don't write through it for bots.

Bot policy: each `BOT_NAMES[i]` owns a deterministic `BOT_COLORS[i]` pair
(`zaxbot/config.py`). At spawn the picked name index is preserved in
scratch (`picked_name_idx`); the spawn payload then (a) writes the
chosen `(c1, c2)` into the bot's pcfg at `*(stats+0x1C)+4/+8` for
persistence, and (b) walks the child-entity appearance path above to
write the floats into `appearance+0xC` / `+0x18` — which the renderer
picks up on the next frame.

CTF preempts `color1` with the team palette at render time — the per-name
write is harmless.

## Character array

The manager character array is at `mgr + 0x290`.

| field | meaning |
|---:|---|
| `mgr + 0x290` | `CEntityCharacter*` array |
| `mgr + 0x294` | live/high-water count used by lookups |
| `mgr + 0x298` | capacity |

Before `sub_59DF90`, the hook pre-grows this array to 16 entries if needed.
This prevents the 9th bot from writing past the initial array allocation.

## On-screen messages

`sub_59B260(char *text, int type)` is a `__stdcall` system-message helper.
With `type == -1`, it posts to all players' message logs. The B menu, spawn
confirmation, and "match full" messages use this path.

Plain embedded C strings are enough; localized string object formatting is only
needed when reusing the engine's own language resources.

## Runtime dumps

Pressing R appends tagged chunks to `zax_dump.bin`:

`snap`, `mgr_root`, `session`, `worldmgr`, `dpmgr`, `idx_nbhd`, per-participant
`part[i]`/`stats[i]`/`cstr[i]`, `charptr`, and per-character `char[i]`.

Chunks use the current 28-byte header:

```text
'ZAX1' magic | tag[16] | src_va | len | payload[len]
```

Use `tools/diffdump.py` to compare snapshots and avoid guessing from static
offsets when runtime data is available.
