"""``detour_5A6E60`` — SK death-pile self-registration.

``sub_5A6E60`` is ``CDropAllOreAndCrystalsAction``'s per-target apply (vtable
0x603578 slot 22): every multiplayer death runs the canned 'Drop Cystals and
Ore' script through it. When the victim carried minerals it clones an
UNNAMED pile entity from the "Ore_Crystals01" model template, places it
collision-aware within 500 px of the corpse (``sub_4EB7B0``), and moves the
whole Ore Deposits + Crystals load into a fresh CollideTrigger on the pile —
touching the pile grants everything to either team and self-deletes it.

The pile carries no entity name (the exe never writes one — unlike the CTF
script-recreated "Red Flag"), so the CTF-style periodic name match cannot see
it. Instead the pile registers ITSELF here, mirroring the portal registration
model: the detour records the DYING CHARACTER's position (entity at
``[esp+8]`` at entry — the placement lands at or near the corpse) into the
``sk_pile_pos`` ring with a TTL, skipping empty-handed deaths via the
engine's own carried-count getter ``sub_426860`` — the exact check the apply
itself performs before spawning anything.

Fires only on an actual death, never per frame; fast-skips (one cmp) outside
armed SK matches. The displaced 6-byte prologue (``sub esp,10h; push ebx;
push ebp; push esi``) is re-executed first so ESP matches what RESUME
(0x5A6E66, ``mov esi,[esp+24h]``) expects; flags are not live across the
entry (the resumed code re-establishes them with ``test esi, esi``).
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    if not (
        layout.has_field('sk_pile_pos')
        and layout.has_field('sk_pile_valid')
        and layout.has_field('sk_pile_next')
        and layout.has_field('sk_routing_active')
    ):
        a.label('detour_5A6E60')
        a.raw(ax.S5A6E60_PROLOGUE)                            # re-exec displaced prologue
        a.jmp_va(ax.S5A6E60_RESUME)
        return

    active_va    = layout.va('sk_routing_active')
    pile_pos_va  = layout.va('sk_pile_pos')
    pile_valid_va = layout.va('sk_pile_valid')
    pile_next_va = layout.va('sk_pile_next')
    pile_ttl_va  = layout.va('sk_pile_ttl')
    def_ore_va   = layout.va('sk_def_ore')
    def_cry_va   = layout.va('sk_def_crystal')
    carry_tmp_va = layout.va('sk_carry_tmp')
    spill_va     = layout.va('sk_spill')
    ring_mask    = cfg.SK_PILE_TABLE_MAX - 1
    assert (cfg.SK_PILE_TABLE_MAX & ring_mask) == 0, 'pile ring needs power-of-two size'

    a.label('detour_5A6E60')
    # Re-execute the displaced prologue so ESP is exactly what RESUME expects;
    # the victim entity then sits at [esp + 0x1C + 8] = [esp + 0x24] pre-pushad.
    a.raw(ax.S5A6E60_PROLOGUE)                                # sub esp,10h; push ebx/ebp/esi
    a.raw(b'\x83\x3D' + le32(active_va) + b'\x00')            # SK match armed?
    a.jz('skpr_resume')

    a.raw(b'\x60')                                            # pushad
    a.raw(b'\x9C')                                            # pushfd
    # entity at entry [esp+8] = [esp + 0x1C (prologue) + 0x24 (pushad+fd) + 8]
    a.raw(b'\x8B\x44\x24\x48')                                # mov eax, [esp+0x48]
    a.raw(b'\x85\xC0'); a.jz('skpr_done')                     # NULL victim
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('skpr_done')         # heap-range sanity
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('skpr_done')
    a.raw(b'\xA3' + le32(spill_va))                           # sk_spill = victim

    # Carried anything? Mirror the apply's own gate: sub_426860 (__usercall
    # ECX=char, EDX=def key -> EAX count) on both mineral defs. An
    # empty-handed death spawns no pile, so don't register a ghost.
    a.raw(b'\xC7\x05' + le32(carry_tmp_va) + le32(0))         # total = 0
    a.raw(b'\x8B\x15' + le32(def_ore_va))                     # edx = ore key
    a.raw(b'\x85\xD2'); a.jz('skpr_cry')                      # unresolved
    a.raw(b'\x8B\x0D' + le32(spill_va))                       # ecx = victim
    a.call_va(ax.SUB_426860_VA)                               # eax = ore count
    a.raw(b'\x01\x05' + le32(carry_tmp_va))                   # total += eax
    a.label('skpr_cry')
    a.raw(b'\x8B\x15' + le32(def_cry_va))                     # edx = crystal key
    a.raw(b'\x85\xD2'); a.jz('skpr_tot')                      # unresolved
    a.raw(b'\x8B\x0D' + le32(spill_va))                       # ecx = victim
    a.call_va(ax.SUB_426860_VA)                               # eax = crystal count
    a.raw(b'\x01\x05' + le32(carry_tmp_va))                   # total += eax
    a.label('skpr_tot')
    a.raw(b'\x83\x3D' + le32(carry_tmp_va) + b'\x00')         # carried anything?
    a.jz('skpr_done')                                         # no pile will spawn

    # Ring write: slot = sk_pile_next & mask; record the corpse position with
    # a fresh TTL; advance the cursor (oldest entry is overwritten when the
    # ring wraps — 8 concurrent piles is already unusual).
    a.raw(b'\xA1' + le32(pile_next_va))                       # eax = cursor
    a.raw(b'\x83\xE0' + bytes([ring_mask]))                   # and eax, mask
    a.raw(b'\x8B\x0D' + le32(spill_va))                       # ecx = victim
    a.raw(b'\x8B\x51' + bytes([ax.ENTITY_POS_X_OFF]))         # edx = raw x bits
    a.raw(b'\x89\x14\xC5' + le32(pile_pos_va))                # pile_pos[slot].x
    a.raw(b'\x8B\x51' + bytes([ax.ENTITY_POS_Y_OFF]))         # edx = raw y bits
    a.raw(b'\x89\x14\xC5' + le32(pile_pos_va + 4))            # pile_pos[slot].y
    a.raw(b'\x8B\x15' + le32(pile_ttl_va))                    # edx = TTL seed
    a.raw(b'\x89\x14\x85' + le32(pile_valid_va))              # pile_valid[slot] = TTL
    if layout.has_field('sk_pile_node') and layout.has_field('sk_pile_dirty'):
        # Bind the pile to its nearest graph node — the source seed of the
        # graph-routed pile field — and flag the field rebuild. The ring
        # slot index must survive wp_find_nearest (clobbers GPRs): spill it
        # in sk_carry_tmp (its carry use above is finished).
        a.raw(b'\xA3' + le32(carry_tmp_va))                   # spill slot idx
        a.raw(b'\x8B\x0C\xC5' + le32(pile_pos_va))            # ecx = pile.x bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch')))
        a.raw(b'\x8B\x0C\xC5' + le32(pile_pos_va + 4))        # ecx = pile.y bits
        a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch') + 4))
        a.call_lbl('wp_find_nearest')                         # ebx = nearest or -1
        a.raw(b'\xA1' + le32(carry_tmp_va))                   # eax = slot idx
        a.raw(b'\x89\x1C\x85' + le32(layout.va('sk_pile_node')))  # node[slot] = ebx
        a.raw(b'\xC7\x05' + le32(layout.va('sk_pile_dirty')) + le32(1))
    a.raw(b'\xFF\x05' + le32(pile_next_va))                   # ++cursor

    a.label('skpr_done')
    a.raw(b'\x9D')                                            # popfd
    a.raw(b'\x61')                                            # popad
    a.label('skpr_resume')
    a.jmp_va(ax.S5A6E60_RESUME)                               # resume at 0x5A6E66
