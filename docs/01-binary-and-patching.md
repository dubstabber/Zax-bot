# 01 — Binary layout & the patch mechanism

## PE image (original, from `Zax.exe.bak`)
- Image base `0x400000`, 32-bit, entry `start` @ `0x5D329F`.
- Segments:
  | name     | start      | end        | perms |
  |----------|------------|------------|-------|
  | `.text`  | 0x401000   | 0x5EA000   | r-x   |
  | `.idata` | 0x5EA000   | 0x5EA34C   | r--   |
  | `.rdata` | 0x5EA34C   | 0x609000   | r--   |
  | `.data`  | 0x609000   | 0x718000   | rw-   |
  | `.rsrc`  | 0x718000   | 0x71A000   | r--   |
- ~11,343 functions; data-driven C++ engine with RTTI-style class-name strings
  (e.g. `CMultiPlayerGameData`) and named properties read from level/config files
  (and the localization file `Polish.red`).
- File↔VA: for `.text`, `file_off = VA - 0x401000 + 0x1000`.

## The `.zaxbot` section (added by the patch)
The patch **appends one new PE section** rather than overwriting existing code:
- Name `.zaxbot`, RVA `0x31A000` → **VA `0x71A000`**, raw file offset **`0x231000`**,
  size `0x1000` (1 page), characteristics `0x60000020` (CODE | EXEC | READ).
- The patcher bumps `NumberOfSections` and `SizeOfImage`, writes a 40-byte section
  header (there is spare room before `SizeOfHeaders`), and appends the page.
- Because the IDB was made from the original image, **the `.zaxbot` section is not in
  IDA**. To inspect built bytes, read `Zax.exe` at file offset `0x231000` directly.

### Current `.zaxbot` layout (Phase 1)
| offset | VA        | contents |
|--------|-----------|----------|
| 0x000  | 0x71A000  | `hook_entry` (the B/R handler) |
| 0x100  | 0x71A100  | `msg_blue` C-string |
| 0x120  | 0x71A120  | `msg_red`  C-string |

Reserve later regions (helpers, bot-request state, names) further down the page.

## The single code patch
At **`0x599A1A`** the original instruction is `call sub_599580` (`E8 61 FB FF FF`).
The patch rewrites only the 5 bytes there to `call hook_entry` (`E8 <rel32 to 0x71A000>`).
`hook_entry` always tail-`jmp`s back to `sub_599580`, so original behavior is preserved.

## `zax_patch.py` guarantees
- Refuses to run without `Zax.exe.bak`; always `shutil.copyfile(BAK, EXE)` first
  (idempotent — re-runs reproduce the same output).
- Asserts the call-site bytes are the expected original `E8 61 FB FF FF` before patching.
- Hand-assembles `hook_entry` with explicit rel8/rel32 fixups; asserts it fits before
  `DATA_OFF` (0x100).

## Verifying a build
```python
# dump & sanity-check the assembled hook
import struct
exe = open('Zax.exe','rb').read()
he  = exe[0x231000:0x231000+0x80]      # hook_entry
# call-site check:
fo  = 0x599A1A - 0x401000 + 0x1000
rel = struct.unpack_from('<i', exe, fo+1)[0]
assert 0x599A1A + 5 + rel == 0x71A000  # -> hook_entry
```
(capstone is not installed in this environment; decode by hand or compute rel targets.)
