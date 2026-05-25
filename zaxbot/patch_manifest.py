"""Patch-site manifest for installing .zaxbot detours into the original PE."""

from . import addresses as ax
from .pe import RelocationPatch


def build_enabled_patches():
    """Return the ordered tuple of RelocationPatch entries that get written
    into the unmodified Zax.exe to redirect engine code into the .zaxbot
    section. The hook payload module (`zaxbot.hook.entry.build_hook`) supplies
    the matching target VAs by label."""
    return (
        RelocationPatch(
            'WM_KEYDOWN hook', 'call', ax.HOOK_SITE_VA,
            b'\xE8\x61\xFB\xFF\xFF', 'hook_entry_va',
        ),
        RelocationPatch(
            'DP poll capture', 'jmp', ax.POLL_VA,
            ax.POLL_PROLOGUE, 'detour_dp_va', 6,
        ),
        RelocationPatch(
            'sub_59DF90 capture/new-match clear', 'jmp', ax.DF90_VA,
            ax.DF90_PROLOGUE, 'detour_df90_va',
        ),
        RelocationPatch(
            'sub_5AA4E0 skip bot camera tracker', 'jmp', ax.SAA4E0_VA,
            b'\x56\x8B\xF1\xE8\x78\x2B\xF5\xFF', 'detour_5AA4E0_va', 8,
        ),
        RelocationPatch(
            'sub_4FBC50 NULL component attach', 'jmp', ax.FBC50_VA,
            b'\x56\x8B\xF1\x8D\x54\x24\x08', 'detour_4FBC50_va', 7,
        ),
        RelocationPatch(
            'sub_542360 bot movement vector', 'jmp', ax.S542360_VA,
            ax.S542360_PROLOGUE, 'detour_542360_va',
        ),
        RelocationPatch(
            'sub_5436F0 bot fire/aim', 'jmp', ax.S5436F0_VA,
            ax.S5436F0_PROLOGUE, 'detour_5436F0_va', 7,
        ),
        RelocationPatch(
            'sub_542550 controller capture', 'jmp', ax.S542550_VA,
            b'\x8B\x44\x24\x04\x56\x8B\xF1', 'detour_542550_va', 7,
        ),
        RelocationPatch(
            'sub_480800 synthetic name-block skip', 'jmp', ax.S480800_NAMEBLK_VA,
            ax.S480800_NAMEBLK_ORIG, 'detour_name_block_skip_va',
        ),
        RelocationPatch(
            'sub_4F5150 char iter null-skip', 'jmp', ax.S4F5204_VA,
            ax.S4F5204_ORIG, 'detour_4F5204_va', 6,
        ),
    )


def apply_patches(image, patches, targets):
    applied = {}
    for patch in patches:
        applied[patch.name] = patch.apply(image, targets)
    return applied
