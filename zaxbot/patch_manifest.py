"""Patch-site manifest for installing .zaxbot detours into the original PE."""

from . import addresses as ax
from .pe import RawBytePatch, RelocationPatch


def build_enabled_patches():
    """Return the ordered tuple of RelocationPatch entries that get written
    into the unmodified Zax.exe to redirect engine code into the .zaxbot
    section. The hook payload module (`zaxbot.hook.entry.build_hook`) supplies
    the matching target VAs by label."""
    from . import config as cfg

    patches = [
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
    ]

    pickup_runtime_enabled = (
        cfg.PICKUP_REGISTER_ENABLED
        or cfg.PICKUP_DIVERT_ENABLED
        or cfg.PICKUP_OVERLAY_MARKERS_ENABLED
    )
    overlay_hook_enabled = cfg.OVERLAY_HOOK_ENABLED or cfg.OVERLAY_ENABLED
    if overlay_hook_enabled or pickup_runtime_enabled:
        # Page-flip detour: draws OVERLAY_WAYPOINTS / OVERLAY_EDGES via the
        # engine's renderer just before the back-buffer is presented. It also
        # owns the once-per-frame world_frame counter used by pickup
        # registration. The full drawing loop is gated at runtime by the
        # overlay_enabled scratch flag; normal builds install the hook for the
        # O-key authoring toggle but start with drawing disabled.
        patches.append(
            RelocationPatch(
                'sub_5693A0 waypoint overlay', 'jmp', ax.S5693A0_VA,
                ax.S5693A0_PROLOGUE, 'detour_5693A0_va', 5,
            )
        )

    if pickup_runtime_enabled:
        # Per-pickup self-registration: detours the CPickupAI per-frame update
        # so each live pickup can record its world position into pickup_table.
        # The detour re-runs the displaced 8-byte prologue (EBX = entity), then
        # fast-skips when pickup_register_enabled is 0. The O-key overlay
        # toggle enables it for item markers; pickup-divert builds keep it on.
        patches.append(
            RelocationPatch(
                'sub_53DA40 pickup registration', 'jmp', ax.S53DA40_VA,
                ax.S53DA40_PROLOGUE, 'detour_53DA40_va', 8,
            )
        )

    if cfg.PORTAL_REGISTER_ENABLED:
        # Teleport/portal self-registration: detours sub_4C11A0 (the single
        # relocate/teleport executor) so every CTeleportAction warp records its
        # source pad into portal_table the moment it fires. Catches conditional
        # and script-driven portals the static Data.dat parse can't see. The
        # detour re-runs the displaced 7-byte prologue (mov eax,[esp+8]; sub
        # esp,0xC) and only does work on an actual teleport, never per frame.
        patches.append(
            RelocationPatch(
                'sub_4C11A0 teleport portal registration', 'jmp', ax.S4C11A0_VA,
                ax.S4C11A0_PROLOGUE, 'detour_4C11A0_va', 7,
            )
        )

    if cfg.CTF_SCORE_GUARD_ENABLED:
        # Harden the CTF capture chain while the scoring team's own flag is
        # away/carried. The inventory-use action is the early consume/feedback
        # path; the score action remains a late numeric-score fallback.
        patches.append(
            RelocationPatch(
                'sub_5B3100 CTF flag-use home-flag guard', 'jmp', ax.S5B3100_VA,
                ax.S5B3100_PROLOGUE, 'detour_5B3100_va', 6,
            )
        )
        patches.append(
            RelocationPatch(
                'sub_5A9960 CTF score home-flag guard', 'jmp', ax.S5A9960_VA,
                ax.S5A9960_PROLOGUE, 'detour_5A9960_va', 9,
            )
        )

    patches.extend([
        # Inline NULL-guard for sub_4FC8A0 (the positional-sound dispatch
        # wrapper). The function does `mov ecx, [ecx+0x48]; call sub_4EA880`
        # — when called on a synthetic-DP bot whose audio emitter at +0x48
        # isn't initialised, ECX comes in as the bot char (or a derived
        # field that's NULL) and the deref faults. The engine's give-weapon
        # path (sub_425590) routes through here unconditionally — neither
        # a5=0 nor a5=1 avoids it — so we patch the function itself to skip
        # the sound when ECX is NULL.
        #
        # Original 21-byte function + 4 padding bytes get replaced with a
        # 25-byte NULL-tolerant version. The trailing 7 padding NOPs after
        # the function are untouched. New `jz +0xE` target is the same `ret`
        # the original `jz +0xA` hit; the second jz (after `test ecx, ecx`)
        # jumps to the same ret. The call rel32 is re-encoded for the new
        # instruction offset.
        RawBytePatch(
            'sub_4FC8A0 NULL-tolerant audio dispatch',
            va=0x4FC8A0,
            original=(
                b'\x8B\x44\x24\x04'      # mov eax, [esp+4]
                b'\x85\xC0'              # test eax, eax
                b'\x74\x0A'              # jz +0xA (to ret)
                b'\x51'                  # push ecx
                b'\x8B\x49\x48'          # mov ecx, [ecx+0x48]  <-- original crash
                b'\x50'                  # push eax
                b'\xE8\xCE\xDF\xFE\xFF'  # call sub_4EA880
                b'\xC2\x04\x00'          # ret 4
                b'\x90\x90\x90\x90'      # 4 of the 11 trailing NOPs
            ),
            replacement=(
                b'\x8B\x44\x24\x04'      # mov eax, [esp+4]
                b'\x85\xC0'              # test eax, eax
                b'\x74\x0E'              # jz +0xE -> 0x4FC8B6 (ret)
                b'\x85\xC9'              # test ecx, ecx        (NEW)
                b'\x74\x0A'              # jz +0xA -> 0x4FC8B6  (NEW)
                b'\x51'                  # push ecx
                b'\x8B\x49\x48'          # mov ecx, [ecx+0x48]
                b'\x50'                  # push eax
                b'\xE8\xCA\xDF\xFE\xFF'  # call sub_4EA880 (rel32 re-encoded)
                b'\xC2\x04\x00'          # ret 4
            ),
        ),
    ])
    return tuple(patches)


def apply_patches(image, patches, targets):
    applied = {}
    for patch in patches:
        applied[patch.name] = patch.apply(image, targets)
    return applied
