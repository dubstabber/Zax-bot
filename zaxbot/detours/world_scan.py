"""``scan_hazards`` and ``pick_pickup`` — world-entity classification helpers.

Both walk the world manager's entity array (``mgr+0x2BC..0x2C0``) — a flat
``DWORD[]`` of entity pointers, distinct from the per-class char array at
``mgr+0x290`` that perception/fire use. They classify each entity via
``sub_416790(this=ent, classdesc)`` (which internally calls ``ent->vtable[2]``
to fetch the entity's actual class). The class descriptors are lazy-init'd
globals — calling the accessor function once before each scan guarantees the
global is populated.

The scan is bounded at 256 iterations regardless of the engine's count field
as a defense against a corrupt mgr; pointer reads pass an image-base range
check (``0x400000..0x700000``) before any deref, mirroring the snapshot loop.

``scan_hazards`` (no args, no return) runs once per match from ``detour_df90``.
It rebuilds ``hazard_table`` from scratch and updates ``hazard_count``.

``pick_pickup`` (no args, no return) runs per-bot per-tick from
``detour_542360`` (staggered via ``ITEM_SCAN_INTERVAL_FRAMES``). Inputs flow
in through scratch (``bot_pos`` / ``bot_char_tmp`` / ``bot_slot_tmp``);
outputs flow back through ``bot_pickup_{x,y}_cache[slot]`` and
``bot_pickup_valid[slot]``.
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    hazard_count_va           = layout.va('hazard_count')
    hazard_table_va           = layout.va('hazard_table')
    hazard_default_radius_va  = layout.va('hazard_default_radius_sq')
    item_radius_va            = layout.va('item_attractor_radius_sq')
    bot_pos_va                = layout.va('bot_pos')
    cand_pos_va               = layout.va('cand_pos')
    cand_tmp_va               = layout.va('cand_tmp')
    curr_dist_sq_va           = layout.va('curr_dist_sq')
    best_target_va            = layout.va('best_target')
    best_dist_sq_va           = layout.va('best_dist_sq')
    # ``best_dx`` and ``best_dy`` are aliased here as "winner_x" / "winner_y"
    # storage — pick_target (the only other consumer of those fields) runs
    # from a different detour entry point, so the lifetime never overlaps.
    winner_x_va               = layout.va('best_dx')
    winner_y_va               = layout.va('best_dy')
    bot_char_tmp_va           = layout.va('bot_char_tmp')
    bot_slot_tmp_va           = layout.va('bot_slot_tmp')
    pickup_x_cache_va         = layout.va('bot_pickup_x_cache')
    pickup_y_cache_va         = layout.va('bot_pickup_y_cache')
    pickup_valid_va           = layout.va('bot_pickup_valid')

    # =====================================================================
    # scan_hazards: rebuild hazard_table from CDamageExpandingRadiusAI
    # entities currently present in the world. Called once per match.
    # =====================================================================
    a.label('scan_hazards')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xC7\x05' + le32(hazard_count_va) + le32(0))        # hazard_count = 0
    a.call_va(ax.CDAMAGE_RADIUS_AI_ACCESSOR_VA)                 # eax = class desc (lazy init)
    a.raw(b'\x85\xC0'); a.jz('scan_haz_done')
    a.raw(b'\x89\xC6')                                          # mov esi, eax (class desc)

    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                   # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('scan_haz_done')
    a.raw(b'\x8B\x88' + le32(ax.WORLDMGR_ENT_COUNT_OFF))        # ecx = ent count
    a.raw(b'\x85\xC9'); a.jz('scan_haz_done')
    a.raw(b'\x8B\x90' + le32(ax.WORLDMGR_ENT_LIST_OFF))         # edx = ent array
    a.raw(b'\x85\xD2'); a.jz('scan_haz_done')

    # Cap iteration at 256 — defense against a corrupt count field.
    a.raw(b'\x81\xF9\x00\x01\x00\x00')                          # cmp ecx, 256
    a.jb('scan_haz_cap_ok')
    a.raw(b'\xB9\x00\x01\x00\x00')                              # mov ecx, 256
    a.label('scan_haz_cap_ok')
    a.raw(b'\x89\xCB')                                          # mov ebx, ecx (end)
    a.raw(b'\x31\xFF')                                          # xor edi, edi (idx)

    a.label('scan_haz_loop')
    a.raw(b'\x8B\x04\xBA')                                      # mov eax, [edx + edi*4]
    a.raw(b'\x85\xC0'); a.jz('scan_haz_next')
    # Image-base range check
    a.raw(b'\x3D\x00\x00\x40\x00')                              # cmp eax, 0x00400000
    a.jb('scan_haz_next')
    a.raw(b'\x3D\x00\x00\x70\x00')                              # cmp eax, 0x00700000
    a.jae('scan_haz_next')

    a.raw(b'\xA3' + le32(cand_tmp_va))                          # save ent ptr

    # sub_416790(this=ent, classdesc) -> AL. May clobber EBX/EDX/EDI/ESI.
    a.raw(b'\x53\x52\x57\x56')                                  # push ebx, edx, edi, esi
    a.raw(b'\x56')                                              # push esi (classdesc arg)
    a.raw(b'\x89\xC1')                                          # mov ecx, eax (this=ent)
    a.call_va(ax.SUB_416790_VA)                                 # ret 4 (pops classdesc)
    a.raw(b'\x5E\x5F\x5A\x5B')                                  # pop esi, edi, edx, ebx
    a.raw(b'\x84\xC0'); a.jz('scan_haz_next')

    # Match — but bail if table is full.
    a.raw(b'\xA1' + le32(hazard_count_va))                      # eax = count
    a.raw(b'\x83\xF8\x20')                                      # cmp eax, 32
    a.jae('scan_haz_done')

    # Read entity position into cand_pos.
    a.raw(b'\x8B\x0D' + le32(cand_tmp_va))                      # mov ecx, ent
    a.raw(b'\x53\x52\x57\x56')                                  # save ebx,edx,edi,esi
    a.raw(b'\x68' + le32(cand_pos_va))                          # push &cand_pos
    a.call_va(ax.SUB_4FB0A0_VA)                                 # __thiscall, ret 4
    a.raw(b'\x5E\x5F\x5A\x5B')                                  # restore esi,edi,edx,ebx

    # Append to hazard_table at hazard_count * 12. Each entry is
    # (x:float, y:float, radius_sq:float).
    a.raw(b'\xA1' + le32(hazard_count_va))                      # eax = count
    a.raw(b'\x8D\x0C\x40')                                      # lea ecx, [eax+eax*2]  (count*3)
    a.raw(b'\xC1\xE1\x02')                                      # shl ecx, 2            (count*12)
    a.raw(b'\x81\xC1' + le32(hazard_table_va))                  # add ecx, hazard_table

    a.raw(b'\xA1' + le32(cand_pos_va))                          # eax = cand_pos.x
    a.raw(b'\x89\x01')                                          # [ecx]   = x
    a.raw(b'\xA1' + le32(cand_pos_va + 4))                      # eax = cand_pos.y
    a.raw(b'\x89\x41\x04')                                      # [ecx+4] = y
    a.raw(b'\xA1' + le32(hazard_default_radius_va))             # eax = default radius²
    a.raw(b'\x89\x41\x08')                                      # [ecx+8] = r²
    a.raw(b'\xFF\x05' + le32(hazard_count_va))                  # ++count

    a.label('scan_haz_next')
    a.raw(b'\x47')                                              # inc edi
    a.raw(b'\x39\xDF')                                          # cmp edi, ebx
    a.jb('scan_haz_loop')

    a.label('scan_haz_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')                                              # ret

    # =====================================================================
    # pick_pickup: per-bot scan for the closest CPickupAI within
    # ITEM_ATTRACTOR_RADIUS_SQ. Writes (x, y) to the bot's pickup cache
    # and sets bot_pickup_valid[slot] = 1 on success (0 otherwise).
    # Inputs (scratch): bot_pos, bot_char_tmp, bot_slot_tmp.
    # =====================================================================
    a.label('pick_pickup')
    a.raw(b'\x60')                                              # pushad

    # Default-invalid for the bot's slot; only the success path bumps it to 1.
    a.raw(b'\x8B\x15' + le32(bot_slot_tmp_va))                  # edx = slot
    a.raw(b'\xC7\x04\x95' + le32(pickup_valid_va) + le32(0))    # pickup_valid[slot] = 0

    a.call_va(ax.CPICKUP_AI_ACCESSOR_VA)                        # eax = class desc
    a.raw(b'\x85\xC0'); a.jz('pp_done')
    a.raw(b'\x89\xC6')                                          # esi = class desc

    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                   # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('pp_done')
    a.raw(b'\x8B\x88' + le32(ax.WORLDMGR_ENT_COUNT_OFF))        # ecx = count
    a.raw(b'\x85\xC9'); a.jz('pp_done')
    a.raw(b'\x8B\x90' + le32(ax.WORLDMGR_ENT_LIST_OFF))         # edx = array
    a.raw(b'\x85\xD2'); a.jz('pp_done')

    a.raw(b'\x81\xF9\x00\x01\x00\x00')                          # cmp ecx, 256
    a.jb('pp_cap_ok')
    a.raw(b'\xB9\x00\x01\x00\x00')                              # mov ecx, 256
    a.label('pp_cap_ok')
    a.raw(b'\x89\xCB')                                          # ebx = end
    a.raw(b'\x31\xFF')                                          # edi = idx

    # Init winner tracker: best_target = NULL, best_dist² = attractor radius²
    # (only candidates strictly closer than the threshold can win).
    a.raw(b'\xC7\x05' + le32(best_target_va) + le32(0))
    a.raw(b'\xA1' + le32(item_radius_va))
    a.raw(b'\xA3' + le32(best_dist_sq_va))

    a.label('pp_loop')
    a.raw(b'\x8B\x04\xBA')                                      # eax = arr[idx]
    a.raw(b'\x85\xC0'); a.jz('pp_next')
    a.raw(b'\x3D\x00\x00\x40\x00')                              # cmp eax, 0x400000
    a.jb('pp_next')
    a.raw(b'\x3D\x00\x00\x70\x00')                              # cmp eax, 0x700000
    a.jae('pp_next')

    a.raw(b'\xA3' + le32(cand_tmp_va))                          # save ent ptr

    # Class check
    a.raw(b'\x53\x52\x57\x56')                                  # save ebx,edx,edi,esi
    a.raw(b'\x56')                                              # push classdesc arg
    a.raw(b'\x89\xC1')                                          # ecx = this
    a.call_va(ax.SUB_416790_VA)                                 # ret 4
    a.raw(b'\x5E\x5F\x5A\x5B')                                  # restore esi,edi,edx,ebx
    a.raw(b'\x84\xC0'); a.jz('pp_next')

    # Pickup found — read its position into cand_pos.
    a.raw(b'\x8B\x0D' + le32(cand_tmp_va))                      # ecx = ent
    a.raw(b'\x53\x52\x57\x56')                                  # save regs
    a.raw(b'\x68' + le32(cand_pos_va))                          # push &cand_pos
    a.call_va(ax.SUB_4FB0A0_VA)                                 # ret 4
    a.raw(b'\x5E\x5F\x5A\x5B')                                  # restore

    # Compute d² = (cand.x - bot.x)² + (cand.y - bot.y)² without touching
    # winner_x / winner_y (those only get written on actual WIN).
    a.raw(b'\xD9\x05' + le32(cand_pos_va))                      # fld cand.x
    a.raw(b'\xD8\x25' + le32(bot_pos_va))                       # fsub bot.x
    a.raw(b'\xD8\xC8')                                          # fmul st,st (dx²)
    a.raw(b'\xD9\x05' + le32(cand_pos_va + 4))                  # fld cand.y
    a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))                   # fsub bot.y
    a.raw(b'\xD8\xC8')                                          # fmul st,st (dy²)
    a.raw(b'\xDE\xC1')                                          # faddp -> ST0=d²
    a.raw(b'\xD9\x15' + le32(curr_dist_sq_va))                  # fst curr_d² (no pop)

    # if d² >= best_d² -> skip (FCOMP pops ST0).
    a.raw(b'\xD8\x1D' + le32(best_dist_sq_va))                  # FCOMP best_d²
    a.raw(b'\xDF\xE0')                                          # fnstsw ax
    a.raw(b'\x9E')                                              # sahf
    a.jae('pp_next')

    # New best — commit the WINNER's absolute (x, y) into winner_x / winner_y
    # so the value survives subsequent losing candidates.
    a.raw(b'\xA1' + le32(cand_tmp_va))
    a.raw(b'\xA3' + le32(best_target_va))
    a.raw(b'\xA1' + le32(curr_dist_sq_va))
    a.raw(b'\xA3' + le32(best_dist_sq_va))
    a.raw(b'\xA1' + le32(cand_pos_va))
    a.raw(b'\xA3' + le32(winner_x_va))
    a.raw(b'\xA1' + le32(cand_pos_va + 4))
    a.raw(b'\xA3' + le32(winner_y_va))

    a.label('pp_next')
    a.raw(b'\x47')                                              # inc edi
    a.raw(b'\x39\xDF')                                          # cmp edi, ebx
    a.jb('pp_loop')

    # No winner -> leave pickup_valid[slot] = 0.
    a.raw(b'\x83\x3D' + le32(best_target_va) + b'\x00')         # cmp best, 0
    a.jz('pp_done')

    # LOS check on winner: sub_491380(this=bot, target=best, 0, NULL, 2, NULL).
    a.raw(b'\x6A\x00')                                          # push 0  (a6)
    a.raw(b'\x6A\x02')                                          # push 2  (a5)
    a.raw(b'\x6A\x00')                                          # push 0  (a4)
    a.raw(b'\x6A\x00')                                          # push 0  (a3)
    a.raw(b'\xFF\x35' + le32(best_target_va))                   # push best (a2)
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))                  # ecx = bot char (this)
    a.call_va(ax.SUB_491380_VA)                                 # __thiscall, ret 0x14
    a.raw(b'\x84\xC0'); a.jz('pp_done')                         # blocked

    # Commit cached pickup target to the bot's slot.
    a.raw(b'\x8B\x15' + le32(bot_slot_tmp_va))                  # edx = slot
    a.raw(b'\xA1' + le32(winner_x_va))
    a.raw(b'\x89\x04\x95' + le32(pickup_x_cache_va))            # pickup_x[slot] = winner_x
    a.raw(b'\xA1' + le32(winner_y_va))
    a.raw(b'\x89\x04\x95' + le32(pickup_y_cache_va))            # pickup_y[slot] = winner_y
    a.raw(b'\xC7\x04\x95' + le32(pickup_valid_va) + le32(1))    # valid[slot] = 1

    a.label('pp_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')                                              # ret
