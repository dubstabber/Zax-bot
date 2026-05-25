# 02 — Keyboard input, the message pump, and the crash

## Main message pump — `sub_5998F0`
Called from `_WinMain@16` (`0x477940`) → this is the **single main thread**, which also
drives fullscreen DirectDraw rendering. There is no separate render thread.

The pump pulls window messages and dispatches by `Msg.message`. Relevant cases:
- `WM_KEYDOWN` (256 / `0x100`) at `loc_599A16`:
  ```
  mov ecx, [esp+Msg.wParam]   ; ECX = VK code
  call sub_599580             ; translate VK -> internal keyid (returns in EAX)
  mov ecx, offset unk_6C1050
  push eax                    ; register key-down with internal keyid
  call sub_4E2A00
  ... (also reads scancode from lParam, calls sub_4E29E0)
  ```
- `WM_KEYUP` (257) and `WM_CHAR` (258) handled nearby; mouse moves update
  `dword_6CFF50/54`; etc. Default path calls `TranslateMessage`/`DispatchMessageA`.

## `sub_599580` — VK → internal keyid translator (the hook site target)
`int __thiscall sub_599580(vk)` (really `vk` arrives in ECX). Pure `switch` with **no
side effects**; maps a subset of VKs (arrows, page keys, F-keys, digits-with-Ctrl) to
internal ids. **VK 0x42 (B) and 0x52 (R) are not in any case → return 0 (default).**
That makes the `call sub_599580` at **`0x599A1A`** an ideal, low-risk interception point:
intercept the call, inspect ECX (the VK), then tail-`jmp sub_599580` so translation still
happens. ECX must be preserved for that tail call.

## The hook (`hook_entry`, Phase 1)
Pseudocode (ECX = VK on entry):
```
if (cl != 'B' && cl != 'R') jmp sub_599580         ; fast path, ECX untouched
pushad                                              ; preserves ECX (= VK) for the tail jmp
  eax = *dword_713F14;            if !eax goto done  ; see doc 03 for the gate
  ecx = eax; edx = *eax; eax = edx[0x184](eax); if !eax goto done
  eax = *(eax + 0x30);            if !eax goto done  ; CMultiPlayerGameData (NULL outside MP)
  sub_59B260(msg, -1)             ; on-screen confirmation (doc 03)
done:
popad
jmp sub_599580                                      ; original translation runs; returns to 0x599A1F
```
`sub_59B260` is `__stdcall` (callee pops its 8 bytes of args), so the stack stays balanced
inside the `pushad/popad` frame.

## Why the previous attempt crashed (solved)
The old `zax_patch.py` hook called **`CreateFileA` + `WriteFile` + `CloseHandle` on every
B/R press** to append to `zax_bot.log`. That synchronous file I/O on the fullscreen-DDraw
main thread (under Wine) closed the game. (The docstring claimed "first press only" but no
such gate existed in the emitted code; the gate chain and the rest of the hook were fine —
logging actually succeeded a few times before the process died.)

**Fix (Phase 1):** do no syscalls / no file I/O in the hook. Give feedback through the
game's own message function `sub_59B260` instead. Verified in-game: no crash, message shows.

**Lesson for later phases:** keep the hook hot-path to game-engine calls that the engine
itself makes on this thread; avoid OS/blocking calls.
