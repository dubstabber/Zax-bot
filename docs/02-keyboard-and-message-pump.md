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

`detect_mode` is intentionally conservative. The previous scan-and-deref build
crashed on `0x6E6F6E00` (`"non\0"`) because a range check does not make an
arbitrary `mov eax, [eax]` safe.

Current behavior:
- re-walk the MP gate to get `mpd`;
- dump `mpd[0..0x200]` to `zax_dump.bin` once per session;
- return `0` (Deathmatch).

To finish mode detection, collect per-mode dumps and identify a known-safe field
holding the active game-type pointer, then compare `*ptr` to:

| mode | vtable |
|---|---:|
| DM | `0x5F0D54` |
| CTF | `0x5EF544` |
| Salvage King | `0x5FED48` |

Do not reintroduce arbitrary pointer scanning without SEH or `IsBadReadPtr`.

## Main-thread rule

Avoid blocking or noisy OS calls on the hot key/frame path. The old per-key log
build was unstable under Wine. Current diagnostic file I/O is limited to explicit
R snapshots and one-shot mode dumps; normal B/digit feedback uses the engine's
own `sub_59B260` message path.

`zax_step.log` is for short spawn progress markers while debugging a crash. Do
not turn it into per-frame logging.
