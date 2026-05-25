# 02 - Keyboard input and main-thread constraints

## Message pump

`sub_5998F0` is the main Win32 message pump called from `_WinMain@16`
(`0x477940`). The game is single-threaded for the important work here: this same
thread drives fullscreen DirectDraw, input processing, and the multiplayer hooks
we call into.

The WM_KEYDOWN path reaches:

```asm
mov ecx, [esp+Msg.wParam]   ; ECX = VK code
call sub_599580             ; VK -> internal key id
mov ecx, offset unk_6C1050
push eax
call sub_4E2A00
```

The patch changes the call at `0x599A1A` to call `.zaxbot:hook_entry`.
`hook_entry` tail-jumps to `sub_599580` on every path, preserving normal key
translation.

## Hook dispatcher

Current dispatcher behavior (`zaxbot/hook/dispatcher.py`):

- idle + **B**: MP-gate, call `detect_mode`, show a prompt with `sub_59B260`,
  set `menu_state = 1`.
- idle + **R**: MP-gate, call `do_snapshot`.
- menu open + digit `1..4`: validate against `max_for_mode[mode]`, store
  `chosen_team`, call `do_spawn_with_team`, close the menu.
- menu open + anything else: close the menu.
- all other keys: tail-jump to `sub_599580`.

The MP gate is:

```c
mgr   = *dword_713F14;
level = mgr->vtbl[0x184](mgr);
mpd   = *(level + 0x30);
```

`mpd == NULL` means "not in a live MP match" for this hook.

## Mode detection

`detect_mode` calls the engine's own active-game-type getter
`sub_59FF90(ecx=mgr)` (found via `sub_5BAD10`, which uses it to emit the
`"gametype"` property string). The returned pointer is the live
`CMultiPlayerGameType`-derived instance; its `[+0]` vtable is one of:

| mode | vtable | returned id |
|---|---:|---:|
| DM | `0x5F0D54` | `0` |
| CTF | `0x5EF544` | `1` |
| Salvage King | `0x5FED48` | `2` |

The earlier "read `[mpd + 0]`" approach was wrong: `mpd` (`[level + 0x30]`)
is the 24-byte `CMultiPlayerGameData` *base* (allocated by `sub_51C010`
with shared vtable `0x5FB104`), so its vtable doesn't distinguish modes.
The historical `0x6E6F6E00` (`"non\0"`) crash came from *scanning* `mpd` and
dereferencing pointer-shaped garbage — different bug, same victim. Do not
reintroduce arbitrary pointer scanning without SEH or `IsBadReadPtr`.

`sub_59FF90`'s `esi` argument is a cache-miss hint that's only used the
first time a game-type resource is loaded; during gameplay the resource is
already cached, so `detect_mode` zeros `esi` defensively before the call.

If the returned vtable matches none of the three, `detect_mode` writes a
one-shot 0x200-byte dump of the game-type object to `zax_dump.bin` for
offline analysis and falls back to DM. In known modes nothing is written
and the message pump stays quiet.

`zaxbot/config.py` exposes a `FORCE_MODE` knob (`None` / `'dm'` / `'ctf'` /
`'sk'`) that short-circuits this whole flow when set — useful for testing
per-mode behaviour without depending on a live session.

## Main-thread rule

Avoid blocking or noisy OS calls on the hot key/frame path. The old per-key log
build was unstable under Wine. Current diagnostic file I/O is limited to explicit
R snapshots and one-shot mode dumps; normal B/digit feedback uses the engine's
own `sub_59B260` message path.

`zax_step.log` is for short spawn progress markers while debugging a crash. Do
not turn it into per-frame logging.
