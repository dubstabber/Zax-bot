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

- **B**: MP-gate, call `detect_mode`, then `build_bot_menu` — the graphical bot
  menu (`zaxbot/hook/bot_menu.py`). It opens a modal dialog built from the
  engine's own widget tree (see the "Bot menu GUI" section below). Buttons
  spawn bots; there is no digit-key step anymore.
- **R**: MP-gate, call `do_snapshot`.
- **O**: MP-gate, toggle `overlay_enabled`, sync pickup-registration markers to
  that state, and show an on-screen confirmation; the page-flip detour is
  installed but drawing starts off.
- **N/J/X/,**: MP-gated waypoint editor controls: drop/snap, select, delete,
  save.
- all other keys: tail-jump to `sub_599580`.

The old text-prompt + digit state machine (`menu_state`, `prompts_table`,
`max_for_mode`, `prompt_*`) was removed; those scratch fields remain as
vestigial reserved space so existing offsets don't shift.

## Bot menu GUI (`build_bot_menu`)

`build_bot_menu` mirrors the in-game Esc quit dialog (`sub_5BF240` ->
`sub_46B050`; the "lose your changes?" confirm dialog `sub_4721B0` is the
closest template). A dialog is a plain `CWindow` (base vtable `0x5EAAC4`) whose
command handler (vtable slot 21) and destructor (slot 0) are overridden — the
same way the confirm dialog derives from the base. The parent is the DESKTOP
ROOT widget = `*(dword_6C02CC + 0x34)` (`sub_4CDF30(uimgr)`), the screen-host
that every pushed screen and dialog attaches to. It is NOT `*dword_713F14`:
that is the `CGame` world manager, not a `CWindow`, so parenting a dialog to it
faults inside `sub_40C6E0` (an early live crash — see the addresses.py note).

At open time the builder `rep movsd`-clones the base vtable into the
`menu_vtable` scratch field and patches slot 0 -> `menu_dtor`, slot 21 ->
`menu_cmd`, allocates a 0x140-byte dialog via `sub_417710`, runs the base ctor
`sub_403D00(dlg, parent, 0)`, then adds a title label plus buttons stacked
vertically with anchor 12 (centered X, below the previous sibling):

- DM / SK: one **Add Bot** button.
- CTF: **Add Blue Bot** + **Add Red Bot**.
- all modes: a **Close** button.

Button pointers are cached in `menu_btn0/1/2` scratch. `menu_cmd` (slot 21)
maps an activated widget: the spawn buttons set `chosen_team` and call
`do_spawn_with_team` (leaving the menu open so several bots can be added),
Close dismisses via vtable slot 5. `menu_dtor` (slot 0) resets the `menu_open`
guard on every teardown path, then runs the base teardown + pooled free
(`sub_54D130(this, 0x140)`) exactly like the confirm dialog's `sub_472300`.
The `menu_open` guard makes a second B while the dialog is up a no-op.

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
