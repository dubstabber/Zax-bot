# 01 - Binary layout and patch mechanism

## Original PE

Original image: `Zax.exe.bak`, image base `0x400000`, 32-bit PE.

Relevant original sections:

| name | VA range | perms |
|---|---:|---|
| `.text` | `0x401000..0x5EA000` | r-x |
| `.idata` | `0x5EA000..0x5EA34C` | r-- |
| `.rdata` | `0x5EA34C..0x609000` | r-- |
| `.data` | `0x609000..0x718000` | rw- |
| `.rsrc` | `0x718000..0x71A000` | r-- |

For `.text`, `file_off = VA - 0x401000 + 0x1000`.

## `.zaxbot`

The patcher appends a new section instead of overwriting free space in existing
sections.

Current values from `zaxbot/config.py`:

| item | value |
|---|---:|
| name | `.zaxbot` |
| RVA / VA | `0x31A000` / `0x71A000` |
| raw file offset | `0x231000` |
| size | `0x8000` bytes |
| characteristics | `0xE0000020` (`CODE | EXEC | READ | WRITE`) |
| code start | `0x71A000` |
| scratch start | `0x71E000` (`SCRATCH_OFF = 0x4000`) |

The IDB is for the original image, so `.zaxbot` bytes must be inspected from
`Zax.exe` at raw offset `0x231000`.

## Patcher layout

`zax_patch.py` only rebuilds and writes the image. The patch is modular:

- `zaxbot/asm.py` - small label/fixup x86 emitter.
- `zaxbot/build.py` and `zaxbot/pe.py` - append section and apply redirects.
- `zaxbot/hook/entry.py` - emits the `.zaxbot` section.
- `zaxbot/patch_manifest.py` - enabled original-image redirects.
- `zaxbot/layout.py` - named scratch fields.

The patcher copies `Zax.exe.bak` to `Zax.exe` first, builds the full patched
image, writes it back, and prints the hook/scratch/detour VAs.

## Enabled redirects

`zaxbot/patch_manifest.py` installs these current redirects:

| original site | purpose |
|---:|---|
| `0x599A1A` | WM_KEYDOWN `call sub_599580` -> `hook_entry` |
| `0x480BD0` | capture DirectPlay manager from DP poll |
| `0x59DF90` | capture `a2`; clear bot scratch arrays on match change |
| `0x5AA4E0` | skip bot camera tracker while spawning |
| `0x4FBC50` | make component attach NULL-tolerant |
| `0x542360` | zero movement vector for bot controllers |
| `0x5436F0` | synthesize bot fire/aim |
| `0x542550` | capture/scrub walking controllers by bot index |
| `0x480889` | skip unsafe `sub_480800` name block for synthetic ids |
| `0x4F5204` | skip NULL entries in a character iterator |

Some older detour emitters still exist for reference or future re-enable, but
only the manifest entries above are patched into `Zax.exe`.

## Build sanity checks

Useful local check after `python3 zax_patch.py`:

```python
import struct
exe = open('Zax.exe', 'rb').read()
fo = 0x599A1A - 0x401000 + 0x1000
rel = struct.unpack_from('<i', exe, fo + 1)[0]
assert 0x599A1A + 5 + rel == 0x71A000
assert exe[0x231000:0x231000 + 8] != b'\x00' * 8
```

When adding a redirect, verify the displaced prologue bytes in
`zaxbot/addresses.py` and add the site to `zaxbot/patch_manifest.py`.
