"""CTF duplicate-flag give guard.

``CGiveDefaultInventoryItemAction``'s per-target give (``sub_5B4DA0``, vtable
slot 28 — the only way a CTF flag ever enters a character's inventory: the
"Picked up a Flag" canned object's enemy-touch branch) has no protection
against TWO characters overlapping the flag's ``CPassThroughTriggerAI`` in the
same frame: each toucher executes the script, each receives a flag item, and
the world flag's ``CDeleteAction`` is deferred/idempotent — live-observed
2026-07-20 as two same-team bots both carrying the red flag. Humans rarely
arrive frame-synchronized; pack-routed bots (goal routing + the dropped-flag
pursuit, which deliberately sends every nearby bot at the drop) do.

The guard: when the def being given is the Red/Blue Flag AND any live
character already carries that def (``sub_426860`` count by key — the action
stores the key at ``action+0x10`` in the exact same id space, see
``addresses.py``), suppress the give. Everything else in the script chain
(delete, sound, dialog, checker deactivate) is idempotent, so the second
toucher simply doesn't get a duplicate. Non-flag gives replay the displaced
prologue and continue unchanged.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


LOW_PTR = 0x00100000
HIGH_PTR = 0x70000000
MAX_GIVE_SCAN_CHARS = 32


def _emit_fallback(a: Asm) -> None:
    a.label('detour_5B4DA0')
    a.raw(ax.S5B4DA0_PROLOGUE)
    a.jmp_va(ax.S5B4DA0_RESUME)


def emit(a: Asm, layout: ScratchLayout) -> None:
    if not cfg.CTF_FLAG_GIVE_GUARD_ENABLED:
        _emit_fallback(a)
        return

    block_count_va = (layout.va('flag_give_block_count')
                      if layout.has_field('flag_give_block_count') else 0)

    # Entry state: ECX = action (this), [esp+4] = target entity, nothing
    # pushed yet (the displaced prologue is the function's FIRST instruction).
    # EBX/ESI/EDI/EBP hold the def key / loop state across the engine calls
    # (MSVC callee-saved; sub_426860 additionally documents preserving them);
    # everything runs inside pushad/popad so the allow path replays the
    # prologue with the original registers intact.
    a.label('detour_5B4DA0')
    a.raw(b'\x60')                                            # pushad
    a.raw(b'\x85\xC9'); a.jz('fgg_allow')                     # action NULL?
    a.raw(b'\x81\xF9' + le32(LOW_PTR)); a.jb('fgg_allow')
    a.raw(b'\x81\xF9' + le32(HIGH_PTR)); a.jae('fgg_allow')
    a.raw(b'\x8B\x59\x10')                                    # ebx = [action+0x10] def key
    a.raw(b'\x83\xFB\xFF'); a.jz('fgg_allow')                 # unset def -> not ours

    # Flag def? Compare against the resolved Red/Blue Flag keys (same id
    # space — the reader fills action+0x10 from item+8, which is what
    # sub_523DF0 returns; see addresses.py).
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68' + le32(ax.BLUE_FLAG_STR_VA))                # push "Blue Flag"
    a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))            # item-def registry
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\x39\xD8')                                        # cmp eax, ebx
    a.jz('fgg_is_flag')
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68' + le32(ax.RED_FLAG_STR_VA))                 # push "Red Flag"
    a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))            # item-def registry
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\x39\xD8')                                        # cmp eax, ebx
    a.jnz('fgg_allow')                                        # not a flag give
    a.label('fgg_is_flag')

    # Sweep the live character array: any existing carrier of this def means
    # the flag is already in someone's hands — a second give would duplicate
    # the only physical flag.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))               # eax = mgr
    a.raw(b'\x85\xC0'); a.jz('fgg_allow')
    a.raw(b'\x3D' + le32(ax.IMAGE_BASE)); a.jb('fgg_allow')
    a.raw(b'\x3D' + le32(HIGH_PTR)); a.jae('fgg_allow')
    a.raw(b'\x8B\xA8\x94\x02\x00\x00')                        # ebp = [mgr+0x294] count
    a.raw(b'\x8B\xB8\x90\x02\x00\x00')                        # edi = [mgr+0x290] array
    a.raw(b'\x85\xFF'); a.jz('fgg_allow')
    a.raw(b'\x81\xFF' + le32(LOW_PTR)); a.jb('fgg_allow')
    a.raw(b'\x81\xFF' + le32(HIGH_PTR)); a.jae('fgg_allow')
    a.raw(b'\x83\xFD' + bytes([MAX_GIVE_SCAN_CHARS]))
    a.jbe('fgg_count_ok')
    a.raw(b'\xBD' + le32(MAX_GIVE_SCAN_CHARS))
    a.label('fgg_count_ok')
    a.raw(b'\x31\xF6')                                        # esi = char index

    a.label('fgg_char_loop')
    a.raw(b'\x39\xEE'); a.jae('fgg_allow')                    # i >= count -> no carrier
    a.raw(b'\x8B\x0C\xB7')                                    # ecx = char = [edi + esi*4]
    a.raw(b'\x85\xC9'); a.jz('fgg_char_next')
    a.raw(b'\x81\xF9' + le32(LOW_PTR)); a.jb('fgg_char_next')
    a.raw(b'\x81\xF9' + le32(HIGH_PTR)); a.jae('fgg_char_next')
    a.raw(b'\x89\xDA')                                        # edx = def key
    a.call_va(ax.SUB_426860_VA)                               # eax = carried count
    a.raw(b'\x85\xC0'); a.jnz('fgg_block')                    # someone has it
    a.label('fgg_char_next')
    a.raw(b'\x46')                                            # ++i
    a.jmp('fgg_char_loop')

    a.label('fgg_block')
    if block_count_va:
        a.raw(b'\xFF\x05' + le32(block_count_va))             # ++blocked (diag)
    a.raw(b'\x61')                                            # popad
    a.raw(b'\x31\xC0')                                        # eax = 0 (give result)
    a.raw(b'\xC2\x04\x00')                                    # ret 4 (skip the give)

    a.label('fgg_allow')
    a.raw(b'\x61')                                            # popad
    a.raw(ax.S5B4DA0_PROLOGUE)
    a.jmp_va(ax.S5B4DA0_RESUME)
