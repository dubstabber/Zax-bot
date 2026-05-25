#!/usr/bin/env python3
"""Zax.exe bot-support patch — thin entrypoint.

The actual patching pipeline lives under ``zaxbot/``:
- ``zaxbot.addresses`` — engine VAs / prologues / vtables / IAT slots.
- ``zaxbot.config``    — bot policy, section sizes, dump format, bot names.
- ``zaxbot.asm``       — label-based x86 emitter.
- ``zaxbot.layout``    — named scratch-field layout for the RWX area.
- ``zaxbot.static_data`` — patch-time-static scratch contents.
- ``zaxbot.pe``        — PE image surgery (append section, redirect call/jmp).
- ``zaxbot.build``     — generic ``build_patched_image`` orchestrator.
- ``zaxbot.patch_manifest`` — ordered redirect manifest for the engine sites.
- ``zaxbot.hook/``     — payload bodies (dispatcher, spawn, snapshot, ...).
- ``zaxbot.detours/``  — one module per detour (dp_poll, bot_fire_aim, ...).

See ``docs/`` for reverse-engineering notes and ``AGENTS.md`` for the
project status.

This module rebuilds ``Zax.exe`` from ``Zax.exe.bak`` on every invocation.
The backup must remain untouched.
"""

import os
import shutil
import sys

# Re-exports for back-compatibility with the test suite + downstream tooling.
from zaxbot import addresses as ax  # noqa: F401
from zaxbot.addresses import (  # noqa: F401
    IMAGE_BASE, HOOK_SITE_VA,
)
from zaxbot.build import build_patched_image as _build_patched_section_image
from zaxbot.config import (  # noqa: F401
    BOT_NAMES, BOT_COLORS, NUM_BOT_NAMES, NAME_SLOT_SIZE, NAME_SLOT_ASCII,
    DUMP_MAGIC, DUMP_TAG_LEN, DUMP_HEADER_SIZE,
    DUMP_FILENAME, DUMP_MSG, FULL_MSG, STEP_FILENAME,
    FIRE_RANGE_SQ,
    SYNTHETIC_ID_LO, SYNTHETIC_ID_HI, MAX_BOT_SLOTS,
    NEW_SECTION_NAME, NEW_SECTION_VA, NEW_SECTION_SIZE,
    SECTION_CHARACTERS, HOOK_ENTRY_OFF, SCRATCH_OFF,
    ZAXBOT_SECTION,
)
from zaxbot.hook.entry import build_hook
from zaxbot.patch_manifest import build_enabled_patches

GAME = '/run/media/ydro/WDC/Games/ZAX'
EXE = os.path.join(GAME, 'Zax.exe')
BAK = os.path.join(GAME, 'Zax.exe.bak')

ENABLED_PATCHES = build_enabled_patches()


def build_patched_image(source_path=BAK):
    """Build the patched PE image. Returns ``(data, info, raw_off, applied)``
    in the same shape the test suite expects."""
    result = _build_patched_section_image(
        source_path,
        IMAGE_BASE,
        ZAXBOT_SECTION,
        build_hook,
        ENABLED_PATCHES,
    )
    return result.data, result.info, result.raw_off, result.applied


def patch_pe():
    if not os.path.exists(BAK):
        print('error: Zax.exe.bak missing; refusing to proceed')
        sys.exit(1)
    shutil.copyfile(BAK, EXE)
    data, info, raw_off, applied = build_patched_image(EXE)
    with open(EXE, 'wb') as f:
        f.write(data)
    abs_section_va = IMAGE_BASE + NEW_SECTION_VA
    new_call = applied['WM_KEYDOWN hook']
    print(f"patched: hook_entry @ VA 0x{info['hook_entry_va']:x} (size {info['hook_entry_size']} B)")
    print(f"  scratch @ 0x{info['scratch_va']:x}  msg @ 0x{info['msg_va']:x}")
    print(f"  .zaxbot: VA 0x{abs_section_va:x} raw 0x{raw_off:x} size 0x{NEW_SECTION_SIZE:x} (RWX)")
    print(f"  call site 0x{HOOK_SITE_VA:x} -> hook_entry ({new_call.hex()})")
    print(f"  detours: dp @ 0x{info['detour_dp_va']:x}  df90 @ 0x{info['detour_df90_va']:x}")


if __name__ == '__main__':
    patch_pe()
