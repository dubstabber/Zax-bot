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
    # Wide heap range — entities live in Wine's userland heap at
    # 0x01xxxxxx..0x05xxxxxx, NOT in the PE-image 0x004xxxxx..0x006xxxxx
    # range. The old narrow filter silently rejected every entity, so this
    # scan and pick_pickup both returned empty. Mirrors snapshot.py's
    # range check at lines 335-338.
    a.raw(b'\x3D\x00\x00\x40\x00')                              # cmp eax, 0x00400000
    a.jb('scan_haz_next')
    a.raw(b'\x3D\x00\x00\x00\x70')                              # cmp eax, 0x70000000
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
    # See scan_haz_loop note: wide heap range, not PE-image range.
    a.raw(b'\x3D\x00\x00\x40\x00')                              # cmp eax, 0x00400000
    a.jb('pp_next')
    a.raw(b'\x3D\x00\x00\x00\x70')                              # cmp eax, 0x70000000
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
    a.raw(b'\xC3')

    # =====================================================================
    # scan_plasma: capture the active map's CPlasmaTileMap* once per match
    # (called from detour_df90, like scan_hazards). Walk the world manager's
    # layer array (mgr+0x2BC[0] == active CLayer) and read the plasma-map
    # pointer. The live-layer field offset is ambiguous (+0x7C vs +0x40), so
    # we try BOTH and VALIDATE each candidate by its vtable: a real
    # CPlasmaTileMap's first dword == off_5FCD98. The validated pointer goes to
    # the plasma_map global (0 on non-plasma maps => is_plasma_at no-ops).
    # The plasma_diag block records LAY, both raw candidates, the chosen ptr,
    # and tilepx/tw/th so the R-snapshot can confirm the pin. pushad/popad,
    # no args/ret (safe to call unconditionally even with a NULL world mgr).
    # =====================================================================
    plasma_map_va   = layout.va('plasma_map')
    plasma_diag_va  = layout.va('plasma_diag')
    plasma_qx_va    = layout.va('plasma_qx')
    plasma_qy_va    = layout.va('plasma_qy')
    plasma_tx_va    = layout.va('plasma_tx')
    plasma_ty_va    = layout.va('plasma_ty')
    plasma_grid_va     = layout.va('plasma_grid')
    plasma_cn_count_va = layout.va('plasma_cn_count')
    plasma_cn_max_va   = layout.va('plasma_cn_max')
    plasma_cn_first_va = layout.va('plasma_cn_first')
    lava_heat_threshold_va = layout.va('lava_heat_threshold')
    lava_dbg_heat_va   = layout.va('lava_dbg_heat')

    a.label('scan_plasma')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xC7\x05' + le32(plasma_map_va) + le32(0))          # plasma_map = 0
    a.raw(b'\xBF' + le32(plasma_diag_va))                       # edi = &plasma_diag
    a.raw(b'\xB9\x14\x00\x00\x00')                              # ecx = 20 dwords (0x50)
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xFC\xF3\xAB')                                      # cld; rep stosd (zero diag)

    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                   # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_done')    # cmp eax,0x400000
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_done')   # cmp eax,0x70000000
    a.raw(b'\x8B\x80' + le32(ax.WORLDMGR_ENT_LIST_OFF))         # eax = [eax+0x2BC] layer_arr
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_done')
    a.raw(b'\x8B\x00')                                          # eax = [layer_arr] (LAY = layer[0])
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_done')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_done')
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x00))                # diag[0] = LAY
    a.raw(b'\x89\xC6')                                          # esi = LAY

    # Record both raw candidates for the pin, then validate.
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_A))        # eax = [esi+0x7C]
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x04))                # diag[1] = cand7C
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_B))        # eax = [esi+0x40]
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x08))                # diag[2] = cand40

    # Candidate A (+0x7C): range-check, then vtable == off_5FCD98.
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_A))        # eax = [esi+0x7C]
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_try_b')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_try_b')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_try_b')
    a.raw(b'\x8B\x10')                                          # edx = [eax] (vtable)
    a.raw(b'\x81\xFA' + le32(ax.CPLASMA_TILEMAP_VTBL_VA))       # cmp edx, off_5FCD98
    a.jz('scan_plasma_store')                                   # match -> eax is the plasma map

    a.label('scan_plasma_try_b')
    a.raw(b'\x8B\x86' + le32(ax.LAYER_PLASMA_MAP_OFF_B))        # eax = [esi+0x40]
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_none')
    a.raw(b'\x3D\x00\x00\x40\x00'); a.jb('scan_plasma_none')
    a.raw(b'\x3D\x00\x00\x00\x70'); a.jae('scan_plasma_none')
    a.raw(b'\x8B\x10')                                          # edx = [eax]
    a.raw(b'\x81\xFA' + le32(ax.CPLASMA_TILEMAP_VTBL_VA))
    a.jz('scan_plasma_store')

    a.label('scan_plasma_none')
    a.raw(b'\x31\xC0')                                          # eax = 0 (no plasma map)

    a.label('scan_plasma_store')
    a.raw(b'\xA3' + le32(plasma_map_va))                        # plasma_map = eax
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x0C))                # diag[3] = chosen
    a.raw(b'\x85\xC0'); a.jz('scan_plasma_done')
    a.raw(b'\x8B\x90' + le32(ax.CPLASMA_TILEPX_W_OFF))          # edx = [eax+0x2D04] tilepx
    a.raw(b'\x89\x15' + le32(plasma_diag_va + 0x10))            # diag[4] = tilepx
    a.raw(b'\x8B\x90' + le32(ax.CPLASMA_TILECNT_W_OFF))         # edx = [eax+0x2D0C] tw
    a.raw(b'\x89\x15' + le32(plasma_diag_va + 0x14))            # diag[5] = tw
    a.raw(b'\x8B\x90' + le32(ax.CPLASMA_TILECNT_H_OFF))         # edx = [eax+0x2D10] th
    a.raw(b'\x89\x15' + le32(plasma_diag_va + 0x18))            # diag[6] = th
    a.label('scan_plasma_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')                                              # ret

    # =====================================================================
    # plasma_tile_xy: convert world (plasma_qx, plasma_qy) -> tile
    # (plasma_tx, plasma_ty) using the active map's tilepx (read at runtime,
    # not hardcoded). Signed idiv matches the engine's sub_540640. NULL/zero
    # tilepx -> tiles 0. Clobbers EAX, ECX, EDX.
    # =====================================================================
    a.label('plasma_tile_xy')
    a.raw(b'\xA1' + le32(plasma_map_va))                        # eax = plasma_map
    a.raw(b'\x85\xC0'); a.jz('ptxy_zero')
    a.raw(b'\x8B\x88' + le32(ax.CPLASMA_TILEPX_W_OFF))          # ecx = [eax+0x2D04] tilepx
    a.raw(b'\x85\xC9'); a.jz('ptxy_zero')                       # div-by-zero guard
    a.raw(b'\xA1' + le32(plasma_qx_va))                         # eax = qx
    a.raw(b'\x99'); a.raw(b'\xF7\xF9')                          # cdq; idiv ecx -> eax = qx/tilepx
    a.raw(b'\xA3' + le32(plasma_tx_va))                         # plasma_tx = tile x
    a.raw(b'\xA1' + le32(plasma_qy_va))                         # eax = qy
    a.raw(b'\x99'); a.raw(b'\xF7\xF9')                          # cdq; idiv ecx -> eax = qy/tilepx
    a.raw(b'\xA3' + le32(plasma_ty_va))                         # plasma_ty = tile y
    a.raw(b'\xC3')
    a.label('ptxy_zero')
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xA3' + le32(plasma_tx_va))
    a.raw(b'\xA3' + le32(plasma_ty_va))
    a.raw(b'\xC3')

    # =====================================================================
    # plasma_get: read one cell of the grid in [plasma_grid] at tile
    # (plasma_tx, plasma_ty). The grid element getter is __thiscall(grid, x, y)
    # at vtable+0xD8, callee-clean (ret 8), bounds-checked (out-of-range -> 0).
    # Works for either embedded grid (footprint @plasma+0x08 or heat
    # @plasma+0x2C6C). Returns EAX = cell value (0 if NULL grid / out of range).
    # Clobbers EAX, ECX; the engine getter preserves EBX/ESI/EDI/EBP.
    # =====================================================================
    a.label('plasma_get')
    a.raw(b'\x8B\x0D' + le32(plasma_grid_va))                   # ecx = plasma_grid
    a.raw(b'\x85\xC9'); a.jz('pget_zero')
    a.raw(b'\xFF\x35' + le32(plasma_ty_va))                     # push ty (arg2 = y)
    a.raw(b'\xFF\x35' + le32(plasma_tx_va))                     # push tx (arg1 = x)
    a.raw(b'\x8B\x01')                                          # eax = [ecx] (grid vtable)
    a.raw(b'\xFF\x90' + le32(ax.CPLASMA_GRID_GETTER_VOFF))      # call [eax+0xD8] (ret 8)
    a.raw(b'\xC3')
    a.label('pget_zero')
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xC3')

    # =====================================================================
    # is_plasma_at: "is world point (plasma_qx, plasma_qy) damaging lava?"
    # Queries the HEAT/elevation grid (plasma+0x2C6C) and returns EAX = 1 when
    # heat >= lava_heat_threshold, else 0. R-snapshot census on Molten Ice
    # established heat >= 128 = molten pool (host burned at 221) vs <=127 ambient
    # walkable floor; the footprint grid (plasma+0x08) is NOT the damage layer
    # (only 10 sparse source cells). Clobbers EAX, ECX, EDX.
    # =====================================================================
    a.label('is_plasma_at')
    a.raw(b'\xA1' + le32(plasma_map_va))                        # eax = plasma_map
    a.raw(b'\x85\xC0'); a.jz('ipa_no')
    a.call_lbl('plasma_tile_xy')                                # plasma_tx/ty = tile(qx,qy)
    a.raw(b'\xA1' + le32(plasma_map_va))                        # eax = plasma_map
    a.raw(b'\x05' + le32(ax.CPLASMA_HEAT_OFF))                  # add eax, 0x2C6C (heat grid)
    a.raw(b'\xA3' + le32(plasma_grid_va))                       # plasma_grid = heat grid
    # WARM-THEN-READ. The engine's storage grid loads a tile's ROW lazily on
    # first access, so the FIRST plasma_get returns a stale value and the SECOND
    # returns the live heat (proven by R-snapshot: 1st heat read < threshold,
    # 2nd read of the SAME tile = 255). Call twice, use the second; the extra
    # read is cheap and makes the query reliable per-frame.
    a.call_lbl('plasma_get')                                    # WARM read (row load) — discard
    a.call_lbl('plasma_get')                                    # REAL read -> eax = live heat (0..255)
    a.raw(b'\xA3' + le32(lava_dbg_heat_va))                     # debug: record the heat seen
    a.raw(b'\x3B\x05' + le32(lava_heat_threshold_va))           # cmp eax, threshold
    a.jb('ipa_no')                                              # heat < threshold -> safe
    a.raw(b'\xB8\x01\x00\x00\x00')                              # eax = 1 (lava)
    a.raw(b'\xC3')
    a.label('ipa_no')
    a.raw(b'\x31\xC0')                                          # eax = 0 (safe)
    a.raw(b'\xC3')

    # =====================================================================
    # plasma_census: scan EVERY tile of the grid in [plasma_grid] and count
    # nonzero cells, tracking the max value and the first nonzero tile. Outputs
    # plasma_cn_count / plasma_cn_max / plasma_cn_first (tx<<16 | ty, or
    # 0xFFFFFFFF if none). Whole-grid coverage is robust to the fire animation
    # and to the host's exact tile, so it definitively shows which embedded grid
    # marks the lava region. pushad/popad; ESI/EDI/EBX survive plasma_get
    # (the engine getter is callee-clean for them).
    # =====================================================================
    a.label('plasma_census')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xC7\x05' + le32(plasma_cn_count_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(plasma_cn_max_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(plasma_cn_first_va) + le32(0xFFFFFFFF))
    a.raw(b'\x8B\x35' + le32(plasma_map_va))                    # esi = plasma_map
    a.raw(b'\x85\xF6'); a.jz('census_done')
    a.raw(b'\x31\xDB')                                          # ebx = ty = 0
    a.label('census_ty')
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_H_OFF))         # eax = th
    a.raw(b'\x39\xC3'); a.jae('census_done')                   # ty >= th -> done (unsigned)
    a.raw(b'\x31\xFF')                                          # edi = tx = 0
    a.label('census_tx')
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_W_OFF))         # eax = tw
    a.raw(b'\x39\xC7'); a.jae('census_tx_done')                # tx >= tw -> row done
    a.raw(b'\x89\x3D' + le32(plasma_tx_va))                     # plasma_tx = tx
    a.raw(b'\x89\x1D' + le32(plasma_ty_va))                     # plasma_ty = ty
    a.call_lbl('plasma_get')                                    # eax = cell value
    a.raw(b'\x85\xC0'); a.jz('census_next')
    a.raw(b'\xFF\x05' + le32(plasma_cn_count_va))               # ++count
    a.raw(b'\x3B\x05' + le32(plasma_cn_max_va))                 # cmp eax, max
    a.jbe('census_skipmax')
    a.raw(b'\xA3' + le32(plasma_cn_max_va))                     # max = eax
    a.label('census_skipmax')
    a.raw(b'\x81\x3D' + le32(plasma_cn_first_va) + b'\xFF\xFF\xFF\xFF')  # cmp first, -1
    a.jnz('census_next')
    a.raw(b'\x89\xF8')                                          # eax = tx
    a.raw(b'\xC1\xE0\x10')                                      # shl eax, 16
    a.raw(b'\x09\xD8')                                          # or  eax, ebx (ty)
    a.raw(b'\xA3' + le32(plasma_cn_first_va))                   # first = tx<<16 | ty
    a.label('census_next')
    a.raw(b'\x47')                                              # inc edi (tx)
    a.jmp('census_tx')
    a.label('census_tx_done')
    a.raw(b'\x43')                                              # inc ebx (ty)
    a.jmp('census_ty')
    a.label('census_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')

    # =====================================================================
    # plasma_dump_heat: write the HEAT grid (plasma+0x2C6C) for every tile into
    # plasma_heatmap as row-major bytes (tw wide, th tall), bounded to the 0x800
    # field. Lets the R-snapshot render the whole lava layout + value
    # distribution so the damage threshold can be read off directly. ESI=plasma,
    # EDI=output cursor, EBX=ty, EBP=tx all survive plasma_get (callee-clean).
    # pushad/popad.
    # =====================================================================
    plasma_heatmap_va = layout.va('plasma_heatmap')
    a.label('plasma_dump_heat')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xBF' + le32(plasma_heatmap_va))                    # edi = &heatmap
    a.raw(b'\xB9\x00\x08\x00\x00')                              # ecx = 0x800
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xFC\xF3\xAA')                                      # cld; rep stosb (zero map)
    a.raw(b'\x8B\x35' + le32(plasma_map_va))                    # esi = plasma_map
    a.raw(b'\x85\xF6'); a.jz('pdh_done')
    a.raw(b'\x89\xF0')                                          # eax = esi
    a.raw(b'\x05' + le32(ax.CPLASMA_HEAT_OFF))                  # add eax, 0x2C6C (heat grid)
    a.raw(b'\xA3' + le32(plasma_grid_va))
    a.raw(b'\xBF' + le32(plasma_heatmap_va))                    # edi = output cursor
    a.raw(b'\x31\xDB')                                          # ebx = ty = 0
    a.label('pdh_ty')
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_H_OFF))         # eax = th
    a.raw(b'\x39\xC3'); a.jae('pdh_done')                       # ty >= th
    a.raw(b'\x31\xED')                                          # ebp = tx = 0
    a.label('pdh_tx')
    a.raw(b'\x81\xFF' + le32(plasma_heatmap_va + 0x800))        # cmp edi, heatmap_end
    a.jae('pdh_done')                                           # bound: don't overflow the field
    a.raw(b'\x8B\x86' + le32(ax.CPLASMA_TILECNT_W_OFF))         # eax = tw
    a.raw(b'\x39\xC5'); a.jae('pdh_tx_done')                    # tx >= tw -> row done
    a.raw(b'\x89\x2D' + le32(plasma_tx_va))                     # plasma_tx = tx (ebp)
    a.raw(b'\x89\x1D' + le32(plasma_ty_va))                     # plasma_ty = ty (ebx)
    a.call_lbl('plasma_get')                                    # eax = heat byte
    a.raw(b'\x88\x07')                                          # [edi] = al
    a.raw(b'\x47')                                              # inc edi
    a.raw(b'\x45')                                              # inc ebp (tx)
    a.jmp('pdh_tx')
    a.label('pdh_tx_done')
    a.raw(b'\x43')                                              # inc ebx (ty)
    a.jmp('pdh_ty')
    a.label('pdh_done')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')                                              # ret
