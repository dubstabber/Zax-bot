"""``detour_5AB9B0`` — proximity-mine deploy self-registration.

``sub_5AB9B0(char)`` is the engine's complete secondary-item deploy (see
``addresses.py``): every mine the HOST executes funnels through it — the
host human's right-click (via the pending-action event ``sub_5AB970``) and
every bot placement (``mine_tick`` calls the patched entry directly).
Remote clients deploy on their own machine, so PC2 mines are NOT observed
here (open; would need the entity-replication path).

The detour predicts at ENTRY whether this call will deploy a MINE: the
char's selected Secondary item must carry the "Proximity Mine" def key at
``[item+8]`` and its can-fire virtual (``item->vtbl[+0x98]`` — pure checks:
reuse delay + rounds + char-state flag, sub_42A4A0 -> sub_5B8020) must
pass. Exactly those conditions make the body deploy, and the mine lands AT
the char's position — so the char's raw ``+0x4C/+0x50`` recorded here IS
the mine position. Success appends into the ``mine_pos``/``mine_ttl`` ring
(TTL mirrors the deployed mine's own ~15 s scripted self-delete) with the
owner slot taken from the ``mine_placing_slot`` handshake ``mine_tick``
sets around its deploy call (0 = no bot placement in flight = the host
human, stored as -1). Do NOT resolve the owner by matching the deploying
char against ``bot_chars[]`` — that table is captured at SPAWN and goes
stale on the first respawn (new char object per life), which live-produced
owner -1 for every post-respawn bot mine and broke the own-mine veto
(2026-07-23 snapshots: place_count 5 with all ring owners -1).

Fires only on an actual secondary-fire attempt, never per frame; fast-skips
(one cmp) while the def key is unresolved (no MP match loaded). Uses its
own ``mreg_*`` temps — the ``mine_tmp_*`` fields belong to ``mine_tick``,
which is LIVE ACROSS this detour when the deploy is bot-initiated.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    if not (
        layout.has_field('mine_def_key')
        and layout.has_field('mine_ttl')
        and layout.has_field('mine_pos')
        and layout.has_field('mreg_char')
    ):
        a.label('detour_5AB9B0')
        a.raw(ax.S5AB9B0_PROLOGUE)                            # re-exec displaced prologue
        a.jmp_va(ax.S5AB9B0_RESUME)
        return

    def_key_va   = layout.va('mine_def_key')
    sec_key_va   = layout.va('mine_sec_key')
    ttl_va       = layout.va('mine_ttl')
    owner_va     = layout.va('mine_owner')
    pos_va       = layout.va('mine_pos')
    next_va      = layout.va('mine_ring_next')
    reg_cnt_va   = layout.va('mine_reg_count')
    mreg_char_va = layout.va('mreg_char')
    mreg_item_va = layout.va('mreg_item')
    ring_mask    = cfg.MINE_TABLE_MAX - 1

    a.label('detour_5AB9B0')
    # Re-execute the displaced prologue so ESP is exactly what RESUME expects;
    # the char arg then sits at [esp + 0x2C + 4] = [esp+0x30] (the resumed
    # code's own `mov edi,[esp+0x30]` reads the same slot).
    a.raw(ax.S5AB9B0_PROLOGUE)                                # sub esp,1Ch; push ebx/ebp/esi/edi
    a.raw(b'\x83\x3D' + le32(def_key_va) + b'\x00')           # mine key resolved?
    a.jz('mreg_resume')

    a.raw(b'\x60')                                            # pushad
    a.raw(b'\x9C')                                            # pushfd
    # char at entry [esp+4] = [esp + 0x2C (prologue) + 0x24 (pushad+fd) + 4]
    a.raw(b'\x8B\x44\x24\x54')                                # mov eax, [esp+0x54]
    a.raw(b'\x85\xC0'); a.jz('mreg_done')                     # NULL char
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('mreg_done')         # heap-range sanity
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('mreg_done')
    a.raw(b'\xA3' + le32(mreg_char_va))                       # mreg_char = char

    # Selected Secondary item, def-key filter, can-fire — mirror the exact
    # gates the body itself applies, so "registered" == "will deploy".
    a.raw(b'\x8B\xC8')                                        # ecx = char
    a.call_va(ax.SUB_4267E0_VA)                               # eax = inventory
    a.raw(b'\x85\xC0'); a.jz('mreg_done')
    a.raw(b'\xA3' + le32(mreg_item_va))                       # spill inv
    a.raw(b'\xFF\x35' + le32(sec_key_va))                     # push group key
    a.raw(b'\x8B\xC8')                                        # ecx = inv
    a.call_va(ax.SUB_425290_VA)                               # eax = sel id / -1
    a.raw(b'\x83\xF8\xFF'); a.jz('mreg_done')                 # nothing selected
    a.raw(b'\x50')                                            # push sel id
    a.raw(b'\x8B\x0D' + le32(mreg_item_va))                   # ecx = inv
    a.raw(b'\x8B\x11')                                        # edx = [inv] vtbl
    a.raw(b'\xFF\x52' + bytes([ax.INVENTORY_GET_WEAPON_OFF])) # call [edx+0x68] -> item
    a.raw(b'\x85\xC0'); a.jz('mreg_done')
    a.raw(b'\x8B\x50' + bytes([ax.ITEM_DEF_KEY_OFF]))         # edx = [item+8] def key
    a.raw(b'\x3B\x15' + le32(def_key_va))                     # the mine def?
    a.jnz('mreg_done')                                        # other secondary (drone)
    a.raw(b'\xA3' + le32(mreg_item_va))                       # spill item
    a.raw(b'\xFF\x35' + le32(mreg_char_va))                   # push char
    a.raw(b'\x8B\xC8')                                        # ecx = item
    a.raw(b'\x8B\x11')                                        # edx = [item] vtbl
    a.raw(b'\xFF\x92' + le32(ax.ITEM_TRY_FIRE_OFF))           # call [edx+0x98] can-fire
    a.raw(b'\x84\xC0'); a.jz('mreg_done')                     # delay/rounds block it

    # Ring append: the mine deploys AT the char's position this same call.
    a.raw(b'\xA1' + le32(next_va))                            # eax = cursor
    a.raw(b'\x83\xE0' + bytes([ring_mask]))                   # and eax, mask
    a.raw(b'\x8B\x0D' + le32(mreg_char_va))                   # ecx = char
    a.raw(b'\x8B\x51' + bytes([ax.ENTITY_POS_X_OFF]))         # edx = raw x bits
    a.raw(b'\x89\x14\xC5' + le32(pos_va))                     # mine_pos[slot].x
    a.raw(b'\x8B\x51' + bytes([ax.ENTITY_POS_Y_OFF]))         # edx = raw y bits
    a.raw(b'\x89\x14\xC5' + le32(pos_va + 4))                 # mine_pos[slot].y
    a.raw(b'\xC7\x04\x85' + le32(ttl_va)
          + le32(max(1, cfg.MINE_TTL_FRAMES)))                # ttl[slot] = seed
    # Owner: the mine_tick handshake (slot+1 announced around its deploy
    # call; 0 = no bot placement in flight, i.e. the host human). decrement
    # maps 0 -> -1 and slot+1 -> slot. A char-pointer sweep against
    # bot_chars[] was live-refuted here: that table is captured at spawn
    # and stale after the first respawn, which attributed every
    # post-respawn bot mine to the human (owner -1) and broke the
    # own-mine veto.
    a.raw(b'\x8B\x15' + le32(layout.va('mine_placing_slot'))) # edx = slot+1 / 0
    a.raw(b'\x4A')                                            # edx = slot / -1
    a.raw(b'\x89\x14\x85' + le32(owner_va))                   # mine_owner[slot] = edx
    a.raw(b'\xFF\x05' + le32(next_va))                        # ++cursor
    a.raw(b'\xFF\x05' + le32(reg_cnt_va))                     # ++mine_reg_count

    a.label('mreg_done')
    a.raw(b'\x9D')                                            # popfd
    a.raw(b'\x61')                                            # popad
    a.label('mreg_resume')
    a.jmp_va(ax.S5AB9B0_RESUME)                               # resume at 0x5AB9B7
