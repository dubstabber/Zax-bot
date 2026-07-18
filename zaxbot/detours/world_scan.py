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
from .. import config as cfg
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
    # load_portals: copy the build-time portal points for the active map into
    # the live portal_table. Called once per match from detour_df90. The
    # static table is parsed from Data.dat at patch-build time; runtime only
    # performs a bounded string match against MAP_NAME_CSTRING_VA.
    # =====================================================================
    if not (
        layout.has_field('portal_table')
        and layout.has_field('portal_static_maps')
        and layout.has_field('portal_static_points')
    ):
        a.label('load_portals')
        a.raw(b'\xC3')
    else:
        portal_count_va             = layout.va('portal_count')
        portal_table_va             = layout.va('portal_table')
        portal_static_map_count_va  = layout.va('portal_static_map_count')
        portal_static_maps_va       = layout.va('portal_static_maps')
        portal_static_points_va     = layout.va('portal_static_points')
        portal_map_stride           = cfg.PORTAL_MAP_NAME_SLOT + 8
        # Portal-routing side tables (destinations). Copied per match in
        # lockstep with the source points; has-dest cleared up front so an
        # unmatched map cannot inherit the previous map's directed edges.
        portal_route_fields = (
            layout.has_field('portal_dest_table')
            and layout.has_field('portal_has_dest')
            and layout.has_field('portal_static_dests')
            and layout.has_field('portal_static_hasdest')
        )

        a.label('load_portals')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(portal_count_va) + le32(0))        # portal_count = 0
        if portal_route_fields:
            a.raw(b'\xBF' + le32(layout.va('portal_has_dest')))     # edi = portal_has_dest
            a.raw(b'\xB9' + le32(cfg.PORTAL_TABLE_MAX))             # ecx = table max
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = [map CString header]
        a.raw(b'\x85\xC0'); a.jz('lp_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = map name ASCII
        a.raw(b'\x80\x38\x00'); a.jz('lp_done')                     # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(portal_static_map_count_va))       # ecx = map_count
        a.raw(b'\x85\xC9'); a.jz('lp_done')
        a.raw(b'\x83\xF9' + bytes([cfg.PORTAL_STATIC_MAP_MAX]))     # cmp ecx, static max
        a.jbe('lp_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.PORTAL_STATIC_MAP_MAX))            # cap corrupt count defensively
        a.label('lp_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('lp_map_loop')
        a.raw(b'\x39\xCE'); a.jae('lp_done')                        # if idx >= map_count
        a.raw(b'\x69\xC6' + le32(portal_map_stride))                # eax = idx * map_stride
        a.raw(b'\x05' + le32(portal_static_maps_va))                # eax = &map_entry[idx]
        a.raw(b'\x89\xC7')                                          # edi = entry
        a.raw(b'\x89\xEA')                                          # edx = active map name
        a.raw(b'\x89\xFB')                                          # ebx = entry name

        a.label('lp_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [entry]
        a.jnz('lp_next_map')
        a.raw(b'\x84\xC0'); a.jz('lp_match')                        # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('lp_str_loop')

        a.label('lp_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('lp_map_loop')

        a.label('lp_match')
        a.raw(b'\x8B\x4F' + bytes([cfg.PORTAL_MAP_NAME_SLOT]))      # ecx = point count
        a.raw(b'\x83\xF9' + bytes([cfg.PORTAL_TABLE_MAX]))          # cmp ecx, live cap
        a.jbe('lp_count_ok')
        a.raw(b'\xB9' + le32(cfg.PORTAL_TABLE_MAX))                 # cap live count
        a.label('lp_count_ok')
        a.raw(b'\x89\x0D' + le32(portal_count_va))                  # portal_count = ecx
        a.raw(b'\x85\xC9'); a.jz('lp_done')
        if portal_route_fields:
            # Copy sources + the parallel dest/has-dest tables. `first` point
            # idx survives in EDX and the capped count in EBX across the three
            # rep movsd blocks (the string loop is done with both registers).
            a.raw(b'\x8B\x57' + bytes([cfg.PORTAL_MAP_NAME_SLOT + 4]))  # edx = first point idx
            a.raw(b'\x89\xCB')                                      # ebx = count
            a.raw(b'\x89\xD6')                                      # esi = first
            a.raw(b'\xC1\xE6\x03')                                  # esi *= 8
            a.raw(b'\x81\xC6' + le32(portal_static_points_va))      # esi = &static_points[first]
            a.raw(b'\xBF' + le32(portal_table_va))                  # edi = live portal_table
            a.raw(b'\x89\xD9')                                      # ecx = count
            a.raw(b'\xD1\xE1')                                      # ecx *= 2 dwords per point
            a.raw(b'\xFC\xF3\xA5')                                  # cld; rep movsd
            a.raw(b'\x89\xD6')                                      # esi = first
            a.raw(b'\xC1\xE6\x03')                                  # esi *= 8
            a.raw(b'\x81\xC6' + le32(layout.va('portal_static_dests')))
            a.raw(b'\xBF' + le32(layout.va('portal_dest_table')))   # edi = live dest table
            a.raw(b'\x89\xD9')                                      # ecx = count
            a.raw(b'\xD1\xE1')                                      # ecx *= 2 dwords per dest
            a.raw(b'\xF3\xA5')                                      # rep movsd
            a.raw(b'\x89\xD6')                                      # esi = first
            a.raw(b'\xC1\xE6\x02')                                  # esi *= 4
            a.raw(b'\x81\xC6' + le32(layout.va('portal_static_hasdest')))
            a.raw(b'\xBF' + le32(layout.va('portal_has_dest')))     # edi = live has-dest
            a.raw(b'\x89\xD9')                                      # ecx = count dwords
            a.raw(b'\xF3\xA5')                                      # rep movsd
        else:
            a.raw(b'\x8B\x77' + bytes([cfg.PORTAL_MAP_NAME_SLOT + 4]))  # esi = first point idx
            a.raw(b'\xC1\xE6\x03')                                  # esi *= 8
            a.raw(b'\x81\xC6' + le32(portal_static_points_va))      # esi = &static_points[first]
            a.raw(b'\xBF' + le32(portal_table_va))                  # edi = live portal_table
            a.raw(b'\xD1\xE1')                                      # ecx *= 2 dwords per point
            a.raw(b'\xFC\xF3\xA5')                                  # cld; rep movsd

        a.label('lp_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

    # =====================================================================
    # bind_portal_nodes: per-match nearest-graph-node bindings for every live
    # pad (portal_node) and, when a destination is known, for its teleport
    # target (portal_dest_node). Also resets the per-bot pad latches and the
    # next-hop spill. Called from detour_df90 AFTER wp_load + load_portals and
    # BEFORE build_flag_routes (bfs_run traverses these bindings as directed
    # edges). pushad/popad, no args.
    # =====================================================================
    if not (
        layout.has_field('portal_node')
        and layout.has_field('portal_dest_node')
        and layout.has_field('portal_has_dest')
        and layout.has_field('bot_portal_target')
        and layout.has_field('pw_spill')
    ):
        a.label('bind_portal_nodes')
        a.raw(b'\xC3')
        a.label('portal_wander_check')
        a.raw(b'\x31\xC0\xC3')                                      # xor eax,eax; ret
    else:
        portal_node_va       = layout.va('portal_node')
        portal_dest_node_va  = layout.va('portal_dest_node')
        portal_has_dest_va   = layout.va('portal_has_dest')
        portal_dest_table_va = layout.va('portal_dest_table')
        bot_portal_target_va = layout.va('bot_portal_target')
        pw_spill_va          = layout.va('pw_spill')
        wp_scratch_va        = layout.va('wp_scratch')
        vcount_va            = layout.va('overlay_vertex_count')

        a.label('bind_portal_nodes')
        a.raw(b'\x60')                                              # pushad
        # Fresh per-match latch state: bot_portal_target + bot_portal_cd +
        # bot_pad_try are contiguous per-bot arrays — one clear covers all.
        a.raw(b'\xBF' + le32(bot_portal_target_va))                 # edi = bot_portal_target
        a.raw(b'\xB9' + le32(3 * cfg.MAX_BOT_SLOTS))                # ecx = 3 arrays x 16
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xFC\xF3\xAB')                                      # cld; rep stosd
        a.raw(b'\xA3' + le32(layout.va('route_portal_hop')))        # route_portal_hop = 0
        # portal_node[] / portal_dest_node[] (contiguous) = -1
        a.raw(b'\xBF' + le32(portal_node_va))                       # edi = portal_node
        a.raw(b'\xB9' + le32(2 * cfg.PORTAL_TABLE_MAX))             # both arrays
        a.raw(b'\x83\xC8\xFF')                                      # eax = -1
        a.raw(b'\xF3\xAB')                                          # rep stosd
        a.raw(b'\x83\x3D' + le32(vcount_va) + b'\x00')              # graph loaded?
        a.jz('bpn_done')
        a.raw(b'\xC7\x05' + le32(pw_spill_va) + le32(0))            # p = 0
        a.label('bpn_loop')
        a.raw(b'\xA1' + le32(pw_spill_va))                          # eax = p
        a.raw(b'\x3B\x05' + le32(layout.va('portal_count')))        # p >= portal_count?
        a.jae('bpn_done')
        a.raw(b'\x83\xF8' + bytes([cfg.PORTAL_TABLE_MAX]))          # p >= table max?
        a.jae('bpn_done')
        # Source pad -> nearest node.
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('portal_table')))    # ecx = pad.x
        a.raw(b'\x89\x0D' + le32(wp_scratch_va))                    # wp_scratch.x
        a.raw(b'\x8B\x0C\xC5' + le32(layout.va('portal_table') + 4))
        a.raw(b'\x89\x0D' + le32(wp_scratch_va + 4))                # wp_scratch.y
        a.call_lbl('wp_find_nearest')                               # ebx = nearest or -1
        a.raw(b'\xA1' + le32(pw_spill_va))                          # eax = p
        a.raw(b'\x89\x1C\x85' + le32(portal_node_va))               # portal_node[p] = ebx
        # Destination (when resolved at build time) -> nearest node.
        a.raw(b'\x83\x3C\x85' + le32(portal_has_dest_va) + b'\x00')
        a.jz('bpn_next')
        a.raw(b'\x8B\x0C\xC5' + le32(portal_dest_table_va))         # ecx = dest.x
        a.raw(b'\x89\x0D' + le32(wp_scratch_va))
        a.raw(b'\x8B\x0C\xC5' + le32(portal_dest_table_va + 4))
        a.raw(b'\x89\x0D' + le32(wp_scratch_va + 4))
        a.call_lbl('wp_find_nearest')                               # ebx = nearest or -1
        a.raw(b'\xA1' + le32(pw_spill_va))                          # eax = p
        a.raw(b'\x89\x1C\x85' + le32(portal_dest_node_va))          # portal_dest_node[p] = ebx
        a.label('bpn_next')
        a.raw(b'\xFF\x05' + le32(pw_spill_va))                      # ++p
        a.jmp('bpn_loop')
        a.label('bpn_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

        # =================================================================
        # portal_wander_check(ECX = current node idx) -> EAX = pad idx+1 to
        # enter, or 0. Called from the follower's random-advance fallback
        # (inside its pushad frame; may clobber GPRs). One RNG roll per
        # arrival: the FIRST active pad bound to this node is rolled against
        # portal_wander_chance — no has-dest requirement (the teleport-jump
        # re-acquire recovers the graph wherever the pad leads).
        # =================================================================
        a.label('portal_wander_check')
        a.raw(b'\x83\x3D' + le32(layout.va('portal_wander_chance')) + b'\x00')
        a.jz('pwc_zero')
        a.raw(b'\x31\xFF')                                          # edi = 0 (p)
        a.label('pwc_loop')
        a.raw(b'\x3B\x3D' + le32(layout.va('portal_count')))        # p >= portal_count?
        a.jae('pwc_zero')
        a.raw(b'\x83\xFF' + bytes([cfg.PORTAL_TABLE_MAX]))          # p >= table max?
        a.jae('pwc_zero')
        a.raw(b'\x39\x0C\xBD' + le32(portal_node_va))               # portal_node[p] == cur?
        a.jnz('pwc_next')
        if layout.has_field('portal_active'):
            a.raw(b'\x83\x3C\xBD' + le32(layout.va('portal_active')) + b'\x00')
            a.jz('pwc_next')                                        # pad currently unusable
        a.raw(b'\x89\x3D' + le32(pw_spill_va))                      # spill p (RNG clobbers)
        a.raw(b'\x6A\x63')                                          # push 99 (high)
        a.raw(b'\x6A\x00')                                          # push 0  (low)
        a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                        # ecx = RNG instance
        a.call_va(ax.RNG_SUB)                                       # eax = 0..99 (callee pops)
        a.raw(b'\x3B\x05' + le32(layout.va('portal_wander_chance')))
        a.jae('pwc_zero')                                           # roll failed -> no enter
        a.raw(b'\xA1' + le32(pw_spill_va))                          # eax = p
        a.raw(b'\x40')                                              # eax = p+1
        a.raw(b'\xC3')                                              # ret
        a.label('pwc_next')
        a.raw(b'\x47')                                              # ++p
        a.jmp('pwc_loop')
        a.label('pwc_zero')
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xC3')

    # =====================================================================
    # load_flags: copy the build-time CTF flag-base points for the active map
    # into the live flag_table. Called once per match from detour_df90, exactly
    # like load_portals — a bounded active-map-name string match against
    # MAP_NAME_CSTRING_VA, then a rep movsd of the matching map's float[2]
    # points. Inert stub when the flag layout fields are absent.
    # =====================================================================
    if not (
        layout.has_field('flag_table')
        and layout.has_field('flag_static_maps')
        and layout.has_field('flag_static_points')
        and layout.has_field('flag_team')
        and layout.has_field('flag_static_team')
    ):
        a.label('load_flags')
        a.raw(b'\xC3')
    else:
        flag_count_va             = layout.va('flag_count')
        flag_table_va             = layout.va('flag_table')
        flag_team_va              = layout.va('flag_team')
        flag_entity_va            = layout.va('flag_entity') if layout.has_field('flag_entity') else 0
        flag_present_va           = layout.va('flag_present') if layout.has_field('flag_present') else 0
        flag_static_map_count_va  = layout.va('flag_static_map_count')
        flag_static_maps_va       = layout.va('flag_static_maps')
        flag_static_points_va     = layout.va('flag_static_points')
        flag_static_team_va       = layout.va('flag_static_team')
        flag_map_stride           = cfg.FLAG_MAP_NAME_SLOT + 8

        a.label('load_flags')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(flag_count_va) + le32(0))          # flag_count = 0
        if flag_entity_va:
            a.raw(b'\xBF' + le32(flag_entity_va))                   # edi = flag_entity
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX * cfg.FLAG_ENTITY_SLOTS_PER_FLAG))
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        if flag_present_va:
            a.raw(b'\xBF' + le32(flag_present_va))                  # edi = flag_present
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))               # ecx = flag slots
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        if layout.has_field('flag_drop_valid') and layout.has_field('bot_drop_target'):
            # Fresh dropped-flag state per match: no known drops, no per-bot
            # pursuit latch/cooldown/patience/best (bot_drop_target through
            # bot_drop_best are contiguous per-bot arrays — one clear covers
            # all four), node binds and route roots back to -1 so a stale
            # drop_dist row can never be consumed on the new map.
            a.raw(b'\xBF' + le32(layout.va('flag_drop_valid')))     # edi = flag_drop_valid
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))               # ecx = flag slots
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
            a.raw(b'\xBF' + le32(layout.va('bot_drop_target')))     # edi = bot_drop_target
            a.raw(b'\xB9' + le32(4 * cfg.MAX_BOT_SLOTS))            # ecx = target+cd+try+best
            a.raw(b'\xF3\xAB')                                      # rep stosd (eax still 0)
            a.raw(b'\xBF' + le32(layout.va('flag_drop_node')))      # edi = flag_drop_node
            a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))               # ecx = flag slots
            a.raw(b'\x83\xC8\xFF')                                  # eax = -1
            a.raw(b'\xF3\xAB')                                      # rep stosd
            a.raw(b'\xBF' + le32(layout.va('drop_route_root')))     # edi = drop_route_root
            a.raw(b'\xB9\x02\x00\x00\x00')                          # ecx = 2 rows
            a.raw(b'\xF3\xAB')                                      # rep stosd (eax still -1)
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = [map CString header]
        a.raw(b'\x85\xC0'); a.jz('lf_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = map name ASCII
        a.raw(b'\x80\x38\x00'); a.jz('lf_done')                     # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(flag_static_map_count_va))         # ecx = map_count
        a.raw(b'\x85\xC9'); a.jz('lf_done')
        a.raw(b'\x83\xF9' + bytes([cfg.FLAG_STATIC_MAP_MAX]))       # cmp ecx, static max
        a.jbe('lf_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.FLAG_STATIC_MAP_MAX))              # cap corrupt count defensively
        a.label('lf_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('lf_map_loop')
        a.raw(b'\x39\xCE'); a.jae('lf_done')                        # if idx >= map_count
        a.raw(b'\x69\xC6' + le32(flag_map_stride))                  # eax = idx * map_stride
        a.raw(b'\x05' + le32(flag_static_maps_va))                  # eax = &map_entry[idx]
        a.raw(b'\x89\xC7')                                          # edi = entry
        a.raw(b'\x89\xEA')                                          # edx = active map name
        a.raw(b'\x89\xFB')                                          # ebx = entry name

        a.label('lf_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [entry]
        a.jnz('lf_next_map')
        a.raw(b'\x84\xC0'); a.jz('lf_match')                        # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('lf_str_loop')

        a.label('lf_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('lf_map_loop')

        a.label('lf_match')
        # edi still = &map_entry[idx]. Read capped point count and the first
        # point index; EBX holds first across both rep-movsd (survives ESI/EDI/
        # ECX clobber).
        a.raw(b'\x8B\x4F' + bytes([cfg.FLAG_MAP_NAME_SLOT]))        # ecx = point count
        a.raw(b'\x83\xF9' + bytes([cfg.FLAG_TABLE_MAX]))            # cmp ecx, live cap
        a.jbe('lf_count_ok')
        a.raw(b'\xB9' + le32(cfg.FLAG_TABLE_MAX))                   # cap live count
        a.label('lf_count_ok')
        a.raw(b'\x89\x0D' + le32(flag_count_va))                    # flag_count = ecx (capped)
        a.raw(b'\x85\xC9'); a.jz('lf_done')
        a.raw(b'\x8B\x5F' + bytes([cfg.FLAG_MAP_NAME_SLOT + 4]))    # ebx = first point idx
        # Copy points: src = &flag_static_points[first*8], dst = flag_table, n = count*2 dwords.
        a.raw(b'\x8D\x34\xDD' + le32(flag_static_points_va))        # lea esi, [ebx*8 + static_points]
        a.raw(b'\xBF' + le32(flag_table_va))                       # edi = live flag_table
        a.raw(b'\xD1\xE1')                                          # ecx *= 2 (dwords per point)
        a.raw(b'\xFC\xF3\xA5')                                      # cld; rep movsd
        # Copy team tags: src = &flag_static_team[first*4], dst = flag_team, n = count dwords.
        a.raw(b'\x8D\x34\x9D' + le32(flag_static_team_va))         # lea esi, [ebx*4 + static_team]
        a.raw(b'\xBF' + le32(flag_team_va))                        # edi = live flag_team
        a.raw(b'\x8B\x0D' + le32(flag_count_va))                   # ecx = flag_count (count)
        a.raw(b'\xF3\xA5')                                         # rep movsd
        if flag_present_va:
            # Flags always start at their bases. From here on flag_present[]
            # is EVENT-owned: the checker activate/deactivate apply detours
            # (detours/flag_events.py) flip it in lockstep with the map
            # script's steal/return/capture transitions.
            a.raw(b'\xBF' + le32(flag_present_va))                  # edi = flag_present
            a.raw(b'\x8B\x0D' + le32(flag_count_va))                # ecx = flag_count
            a.raw(b'\xB8\x01\x00\x00\x00')                          # eax = 1
            a.raw(b'\xF3\xAB')                                      # rep stosd

        a.label('lf_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

    # =====================================================================
    # load_doors: copy the build-time door centers for the active map into the
    # live door_table. Called once per match from detour_df90, exactly like
    # load_portals/load_flags — a bounded active-map-name string match against
    # MAP_NAME_CSTRING_VA, then a rep movsd of the matching map's float[2]
    # points. Also resets the live door state: door_blocked[] to 0 (the first
    # periodic grid scan repopulates it within ~1 frame — the countdown is
    # seeded to 1 on match change) and the per-bot wedge-door latch to -1.
    # Inert stub when the door layout fields are absent.
    #
    # door_capture_wedge: called by the movement detour the moment it marks a
    # failed edge — finds the nearest CURRENTLY-BLOCKED door within
    # door_wedge_radius_sq of the bot (which is physically pressed against the
    # obstacle right then) and latches its index into route_block_door[slot]
    # (-1 = none). The follower's fast-retry check clears the failed-edge
    # marker as soon as that door reads passable again. pushad/popad, no
    # args/ret; inputs flow through scratch (bot_pos / bot_slot_tmp).
    # =====================================================================
    if not (
        layout.has_field('door_table')
        and layout.has_field('door_blocked')
        and layout.has_field('door_entity')
        and layout.has_field('route_block_door')
        and layout.has_field('door_static_maps')
        and layout.has_field('door_static_points')
        and layout.has_field('door_static_flags')
        and layout.has_field('door_static_openers')
        and layout.has_field('door_flags')
        and layout.has_field('door_opener')
        and layout.has_field('door_opener_count')
    ):
        a.label('load_doors')
        a.raw(b'\xC3')
        a.label('door_capture_wedge')
        a.raw(b'\xC3')
        a.label('door_refresh_state')
        a.raw(b'\xC3')
        a.label('build_edge_doors')
        a.raw(b'\xC3')
    else:
        door_count_va             = layout.va('door_count')
        door_table_va             = layout.va('door_table')
        door_blocked_va           = layout.va('door_blocked')
        door_entity_va            = layout.va('door_entity')
        route_block_door_va       = layout.va('route_block_door')
        door_static_map_count_va  = layout.va('door_static_map_count')
        door_static_maps_va       = layout.va('door_static_maps')
        door_static_points_va     = layout.va('door_static_points')
        door_static_flags_va      = layout.va('door_static_flags')
        door_static_openers_va    = layout.va('door_static_openers')
        door_flags_va             = layout.va('door_flags')
        door_opener_va            = layout.va('door_opener')
        door_opener_count_va      = layout.va('door_opener_count')
        door_wedge_radius_va      = layout.va('door_wedge_radius_sq')
        door_tmp_d2_va            = layout.va('door_tmp_d2')
        door_tmp_best_va          = layout.va('door_tmp_best')
        door_dirty_va             = layout.va('door_dirty')
        door_rebuild_cd_va        = layout.va('door_rebuild_cd')
        bot_slot_tmp2_va          = layout.va('bot_slot_tmp')
        # Map records carry two (count, first) pairs: points then openers.
        door_map_stride           = cfg.DOOR_MAP_NAME_SLOT + 16
        door_slots                = max(1, cfg.DOOR_ENTITY_SLOTS_PER_DOOR)

        a.label('load_doors')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(door_count_va) + le32(0))          # door_count = 0
        a.raw(b'\xC7\x05' + le32(door_opener_count_va) + le32(0))   # opener_count = 0
        a.raw(b'\xC7\x05' + le32(door_dirty_va) + le32(0))          # door_dirty = 0
        a.raw(b'\xC7\x05' + le32(door_rebuild_cd_va) + le32(0))     # rebuild cooldown = 0
        a.raw(b'\xBF' + le32(door_blocked_va))                      # edi = door_blocked
        a.raw(b'\xB9' + le32(cfg.DOOR_TABLE_MAX))                   # ecx = live cap
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xFC\xF3\xAB')                                      # cld; rep stosd
        a.raw(b'\xBF' + le32(door_entity_va))                       # edi = door_entity cache
        a.raw(b'\xB9' + le32(cfg.DOOR_TABLE_MAX * door_slots))      # ecx = cache slots
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xF3\xAB')                                          # rep stosd
        a.raw(b'\xBF' + le32(door_flags_va))                        # edi = door_flags (bytes)
        a.raw(b'\xB9' + le32(cfg.DOOR_TABLE_MAX))                   # ecx = live cap
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xF3\xAA')                                          # rep stosb
        a.raw(b'\xBF' + le32(route_block_door_va))                  # edi = route_block_door
        a.raw(b'\xB9' + le32(cfg.MAX_BOT_SLOTS))                    # ecx = bot slots
        a.raw(b'\x83\xC8\xFF')                                      # or eax, -1
        a.raw(b'\xF3\xAB')                                          # rep stosd (all -1)
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = [map CString header]
        a.raw(b'\x85\xC0'); a.jz('ldo_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = map name ASCII
        a.raw(b'\x80\x38\x00'); a.jz('ldo_done')                    # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(door_static_map_count_va))         # ecx = map_count
        a.raw(b'\x85\xC9'); a.jz('ldo_done')
        a.raw(b'\x83\xF9' + bytes([cfg.DOOR_STATIC_MAP_MAX]))       # cmp ecx, static max
        a.jbe('ldo_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.DOOR_STATIC_MAP_MAX))              # cap corrupt count defensively
        a.label('ldo_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('ldo_map_loop')
        a.raw(b'\x39\xCE'); a.jae('ldo_done')                       # if idx >= map_count
        a.raw(b'\x69\xC6' + le32(door_map_stride))                  # eax = idx * map_stride
        a.raw(b'\x05' + le32(door_static_maps_va))                  # eax = &map_entry[idx]
        a.raw(b'\x89\xC7')                                          # edi = entry
        a.raw(b'\x89\xEA')                                          # edx = active map name
        a.raw(b'\x89\xFB')                                          # ebx = entry name

        a.label('ldo_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [entry]
        a.jnz('ldo_next_map')
        a.raw(b'\x84\xC0'); a.jz('ldo_match')                       # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('ldo_str_loop')

        a.label('ldo_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('ldo_map_loop')

        a.label('ldo_match')
        # DOOR_TABLE_MAX (192) exceeds a sign-extended imm8, so the live cap
        # compare must use the imm32 form (81 /7), unlike the portal/flag caps.
        a.raw(b'\x89\xFD')                                          # ebp = map record (name done)
        a.raw(b'\x8B\x4F' + bytes([cfg.DOOR_MAP_NAME_SLOT]))        # ecx = point count
        a.raw(b'\x81\xF9' + le32(cfg.DOOR_TABLE_MAX))               # cmp ecx, live cap
        a.jbe('ldo_count_ok')
        a.raw(b'\xB9' + le32(cfg.DOOR_TABLE_MAX))                   # cap live count
        a.label('ldo_count_ok')
        a.raw(b'\x89\x0D' + le32(door_count_va))                    # door_count = ecx
        a.raw(b'\x85\xC9'); a.jz('ldo_done')
        a.raw(b'\x8B\x5D' + bytes([cfg.DOOR_MAP_NAME_SLOT + 4]))    # ebx = first point idx
        # Points: src = &static_points[first*8], n = count*2 dwords.
        a.raw(b'\x8D\x34\xDD' + le32(door_static_points_va))        # lea esi, [ebx*8 + points]
        a.raw(b'\xBF' + le32(door_table_va))                        # edi = live door_table
        a.raw(b'\xD1\xE1')                                          # ecx *= 2 dwords per point
        a.raw(b'\xFC\xF3\xA5')                                      # cld; rep movsd
        # Flags: src = &static_flags[first], n = door_count bytes.
        a.raw(b'\x8B\x0D' + le32(door_count_va))                    # ecx = door_count
        a.raw(b'\x8D\xB3' + le32(door_static_flags_va))             # lea esi, [ebx + flags]
        a.raw(b'\xBF' + le32(door_flags_va))                        # edi = live door_flags
        a.raw(b'\xF3\xA4')                                          # rep movsb
        # Openers: count/first from the second record pair.
        a.raw(b'\x8B\x4D' + bytes([cfg.DOOR_MAP_NAME_SLOT + 8]))    # ecx = opener count
        a.raw(b'\x83\xF9' + bytes([cfg.DOOR_OPENER_TABLE_MAX]))     # cmp ecx, live cap
        a.jbe('ldo_op_count_ok')
        a.raw(b'\xB9' + le32(cfg.DOOR_OPENER_TABLE_MAX))            # cap live count
        a.label('ldo_op_count_ok')
        a.raw(b'\x89\x0D' + le32(door_opener_count_va))             # opener_count = ecx
        a.raw(b'\x85\xC9'); a.jz('ldo_done')
        a.raw(b'\x8B\x5D' + bytes([cfg.DOOR_MAP_NAME_SLOT + 12]))   # ebx = first opener idx
        a.raw(b'\xC1\xE3\x04')                                      # ebx *= 16 (record stride)
        a.raw(b'\x8D\xB3' + le32(door_static_openers_va))           # lea esi, [ebx + openers]
        a.raw(b'\xBF' + le32(door_opener_va))                       # edi = live door_opener
        a.raw(b'\xC1\xE1\x02')                                      # ecx = count*4 dwords
        a.raw(b'\xF3\xA5')                                          # rep movsd

        a.label('ldo_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # door_capture_wedge
        # -----------------------------------------------------------------
        a.label('door_capture_wedge')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp2_va))                 # ecx = slot
        a.raw(b'\xC7\x04\x8D' + le32(route_block_door_va)
              + b'\xFF\xFF\xFF\xFF')                                # latch = -1
        a.raw(b'\xA1' + le32(door_wedge_radius_va))                 # eax = wedge radius^2
        a.raw(b'\xA3' + le32(door_tmp_best_va))                     # best = radius^2
        a.raw(b'\x83\xCB\xFF')                                      # ebx = -1 (best idx)
        a.raw(b'\x31\xF6')                                          # esi = door idx

        a.label('dcw_loop')
        a.raw(b'\x3B\x35' + le32(door_count_va))                    # cmp esi, [door_count]
        a.jae('dcw_done')
        a.raw(b'\x83\x3C\xB5' + le32(door_blocked_va) + b'\x00')    # door currently blocked?
        a.jz('dcw_next')                                            # open doors can't be the wedge
        # d2 = (door[i].x - bot.x)^2 + (door[i].y - bot.y)^2
        a.raw(b'\xD9\x04\xF5' + le32(door_table_va))                # fld door.x
        a.raw(b'\xD8\x25' + le32(bot_pos_va))                       # fsub bot.x
        a.raw(b'\xD8\xC8')                                          # fmul st,st
        a.raw(b'\xD9\x04\xF5' + le32(door_table_va + 4))            # fld door.y
        a.raw(b'\xD8\x25' + le32(bot_pos_va + 4))                   # fsub bot.y
        a.raw(b'\xD8\xC8')                                          # fmul st,st
        a.raw(b'\xDE\xC1')                                          # faddp -> st0 = d2
        a.raw(b'\xD9\x15' + le32(door_tmp_d2_va))                   # fst d2 (keep)
        a.raw(b'\xD8\x1D' + le32(door_tmp_best_va))                 # fcomp best (pop)
        a.raw(b'\xDF\xE0\x9E')                                      # fnstsw ax; sahf
        a.jae('dcw_next')                                           # d2 >= best -> not nearer
        a.raw(b'\xA1' + le32(door_tmp_d2_va))                       # best = d2
        a.raw(b'\xA3' + le32(door_tmp_best_va))
        a.raw(b'\x89\xF3')                                          # ebx = esi (best idx)
        a.label('dcw_next')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('dcw_loop')

        a.label('dcw_done')
        a.raw(b'\x83\xFB\xFF')                                      # found one?
        a.jz('dcw_out')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp2_va))                 # ecx = slot
        a.raw(b'\x89\x1C\x8D' + le32(route_block_door_va))          # latch = door idx
        a.label('dcw_out')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # door_refresh_state: PER-FRAME (page flip) re-read of the cached
        # anchor entities' SOLID bit into door_blocked[]. The periodic grid
        # walk only maintains the door_entity cache; deriving state from the
        # walk itself was live-tested and rejected (the walk interval is in
        # FRAMES, so overlay-induced low FPS stretched it to many seconds and
        # door rings looked permanently stale). Any change sets door_dirty so
        # the open-route BFS field can be rebuilt (debounced). pushad/popad.
        # -----------------------------------------------------------------
        assert door_slots == 3, 'door_refresh_state unrolled store expects 3 slots'
        a.label('door_refresh_state')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\x83\x3D' + le32(door_count_va) + b'\x00')          # any doors?
        a.jz('drs_done')
        a.raw(b'\x31\xF6')                                          # esi = door idx
        a.label('drs_loop')
        a.raw(b'\x3B\x35' + le32(door_count_va))                    # cmp esi, [door_count]
        a.jae('drs_done')
        a.raw(b'\x31\xDB')                                          # ebx = 0 (blocked?)
        a.raw(b'\x8D\x0C\x76')                                      # lea ecx, [esi + esi*2]
        for k in range(door_slots):
            a.raw(b'\x8B\x04\x8D' + le32(door_entity_va + 4 * k))   # eax = cache[i*3 + k]
            a.raw(b'\x85\xC0'); a.jz(f'drs_k{k}_skip')              # empty slot
            a.raw(b'\x3D\x00\x00\x40\x00'); a.jb(f'drs_k{k}_skip')  # below heap range
            a.raw(b'\x3D\x00\x00\x00\x70'); a.jae(f'drs_k{k}_skip') # above heap range
            a.raw(b'\xF7\x40' + bytes([ax.ENTITY_FLAGS_OFF])
                  + le32(ax.ENTITY_SOLID_BIT))                      # test [ent+0x1C], SOLID
            a.jz(f'drs_k{k}_skip')
            a.raw(b'\xBB\x01\x00\x00\x00')                          # ebx = 1
            a.label(f'drs_k{k}_skip')
        a.raw(b'\x3B\x1C\xB5' + le32(door_blocked_va))              # cmp ebx, door_blocked[i]
        a.jz('drs_next')
        a.raw(b'\x89\x1C\xB5' + le32(door_blocked_va))              # door_blocked[i] = ebx
        a.raw(b'\xC7\x05' + le32(door_dirty_va) + le32(1))          # door_dirty = 1
        a.label('drs_next')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('drs_loop')
        a.label('drs_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

        # -----------------------------------------------------------------
        # build_edge_doors: STATIC per-match edge->door adjacency. For each
        # graph edge, record the nearest door whose center is within
        # door_edge_radius_sq of the edge SEGMENT (true point-segment
        # distance: t = clamp((D-P).seg/|seg|^2, 0, 1); d^2 = |(D-P)-t*seg|^2)
        # into edge_door[e] (-1 = none). Doors and the graph never move
        # mid-match, so this runs once per match from detour_df90 (after
        # wp_load + load_doors); the BFS open-field rebuilds then consult
        # edge_door[] + live door_blocked[] with pure integer reads.
        # pushad/popad. Reuses the follower's wp_seg_x/y per-call temps
        # (single-threaded; no interleaving with a follower think).
        # -----------------------------------------------------------------
        if not (layout.has_field('edge_door')
                and layout.has_field('overlay_edges')
                and layout.has_field('overlay_vertices')):
            a.label('build_edge_doors')
            a.raw(b'\xC3')
        else:
            edge_door_va   = layout.va('edge_door')
            edges_va       = layout.va('overlay_edges')
            verts_va       = layout.va('overlay_vertices')
            ecount_va      = layout.va('overlay_edge_count')
            vcount_va      = layout.va('overlay_vertex_count')
            wp_seg_x_va    = layout.va('wp_seg_x')
            wp_seg_y_va    = layout.va('wp_seg_y')
            bed_len2_va    = layout.va('bed_len2')
            bed_rx_va      = layout.va('bed_rx')
            bed_ry_va      = layout.va('bed_ry')
            bed_d2_va      = layout.va('bed_d2')
            bed_best_va    = layout.va('bed_best')
            edge_radius_va = layout.va('door_edge_radius_sq')

            edge_pass_va = layout.va('edge_pass')
            a.label('build_edge_doors')
            a.raw(b'\x60')                                          # pushad
            a.raw(b'\xBF' + le32(edge_door_va))                     # edi = edge_door
            a.raw(b'\xB9' + le32(cfg.OVERLAY_EDGE_MAX))             # ecx = edge cap
            a.raw(b'\x83\xC8\xFF')                                  # or eax, -1
            a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
            a.raw(b'\xBF' + le32(edge_pass_va))                     # edi = edge_pass (bytes)
            a.raw(b'\xB9' + le32(cfg.OVERLAY_EDGE_MAX))             # ecx = edge cap
            a.raw(b'\xB0\x0F')                                      # al = 0x0F (both ways, both teams)
            a.raw(b'\xF3\xAA')                                      # rep stosb
            a.raw(b'\x83\x3D' + le32(door_count_va) + b'\x00')      # any doors?
            a.jz('bed_done')
            a.raw(b'\x83\x3D' + le32(ecount_va) + b'\x00')          # any edges?
            a.jz('bed_done')
            a.raw(b'\x31\xF6')                                      # esi = edge idx
            a.label('bed_edge_loop')
            a.raw(b'\x3B\x35' + le32(ecount_va))                    # cmp esi, [edge_count]
            a.jae('bed_done')
            a.raw(b'\x0F\xB7\x04\xB5' + le32(edges_va))             # movzx eax, word (i)
            a.raw(b'\x0F\xB7\x14\xB5' + le32(edges_va + 2))         # movzx edx, word (j)
            a.raw(b'\x3B\x05' + le32(vcount_va)); a.jae('bed_edge_next')
            a.raw(b'\x3B\x15' + le32(vcount_va)); a.jae('bed_edge_next')
            a.raw(b'\x8D\x04\xC5' + le32(verts_va))                 # eax = &verts[i] (P)
            a.raw(b'\x8D\x14\xD5' + le32(verts_va))                 # edx = &verts[j] (C)
            # seg = C - P
            a.raw(b'\xD9\x02')                                      # fld C.x
            a.raw(b'\xD8\x20')                                      # fsub [eax] (P.x)
            a.raw(b'\xD9\x1D' + le32(wp_seg_x_va))                  # fstp seg_x
            a.raw(b'\xD9\x42\x04')                                  # fld C.y
            a.raw(b'\xD8\x60\x04')                                  # fsub [eax+4] (P.y)
            a.raw(b'\xD9\x1D' + le32(wp_seg_y_va))                  # fstp seg_y
            # len2 = seg.seg; zero-length edge -> no door binding
            a.raw(b'\xD9\x05' + le32(wp_seg_x_va)); a.raw(b'\xD8\xC8')
            a.raw(b'\xD9\x05' + le32(wp_seg_y_va)); a.raw(b'\xD8\xC8')
            a.raw(b'\xDE\xC1')                                      # faddp -> len2
            a.raw(b'\xD9\x1D' + le32(bed_len2_va))                  # fstp bed_len2
            a.raw(b'\x83\x3D' + le32(bed_len2_va) + b'\x00')        # +0.0 bits == 0?
            a.jz('bed_edge_next')                                   # degenerate edge
            a.raw(b'\x8B\x0D' + le32(edge_radius_va))               # best = edge radius^2
            a.raw(b'\x89\x0D' + le32(bed_best_va))
            a.raw(b'\x83\xCB\xFF')                                  # ebx = -1 (best door)
            a.raw(b'\x31\xFF')                                      # edi = door idx
            a.label('bed_door_loop')
            a.raw(b'\x3B\x3D' + le32(door_count_va))                # cmp edi, [door_count]
            a.jae('bed_commit')
            # rel = D - P
            a.raw(b'\xD9\x04\xFD' + le32(door_table_va))            # fld door.x
            a.raw(b'\xD8\x20')                                      # fsub P.x
            a.raw(b'\xD9\x1D' + le32(bed_rx_va))                    # fstp rel.x
            a.raw(b'\xD9\x04\xFD' + le32(door_table_va + 4))        # fld door.y
            a.raw(b'\xD8\x60\x04')                                  # fsub P.y
            a.raw(b'\xD9\x1D' + le32(bed_ry_va))                    # fstp rel.y
            # t = clamp((rel.seg)/len2, 0, 1)
            a.raw(b'\xD9\x05' + le32(bed_rx_va))
            a.raw(b'\xD8\x0D' + le32(wp_seg_x_va))                  # rel.x * seg.x
            a.raw(b'\xD9\x05' + le32(bed_ry_va))
            a.raw(b'\xD8\x0D' + le32(wp_seg_y_va))                  # rel.y * seg.y
            a.raw(b'\xDE\xC1')                                      # faddp -> dot
            a.raw(b'\xD8\x35' + le32(bed_len2_va))                  # fdiv len2 -> t
            a.raw(b'\xD9\xE8')                                      # fld1 (ST0=1, ST1=t)
            a.raw(b'\xDF\xF1')                                      # fcomip (pop 1); CF=1 iff 1<t
            a.jae('bed_t_no_hi')
            a.raw(b'\xDD\xD8'); a.raw(b'\xD9\xE8')                  # t = 1
            a.label('bed_t_no_hi')
            a.raw(b'\xD9\xEE')                                      # fldz (ST0=0, ST1=t)
            a.raw(b'\xDF\xF1')                                      # fcomip (pop 0); CF=1 iff 0<t
            a.jb('bed_t_no_lo')
            a.raw(b'\xDD\xD8'); a.raw(b'\xD9\xEE')                  # t = 0
            a.label('bed_t_no_lo')
            # d2 = (rel.x - t*seg.x)^2 + (rel.y - t*seg.y)^2   (ST0 = t)
            a.raw(b'\xD9\xC0')                                      # fld st0 (dup t)
            a.raw(b'\xD8\x0D' + le32(wp_seg_x_va))                  # t*seg.x
            a.raw(b'\xD8\x2D' + le32(bed_rx_va))                    # fsubr -> rel.x - t*seg.x
            a.raw(b'\xD8\xC8')                                      # ^2
            a.raw(b'\xD9\xC1')                                      # fld st1 (t)
            a.raw(b'\xD8\x0D' + le32(wp_seg_y_va))                  # t*seg.y
            a.raw(b'\xD8\x2D' + le32(bed_ry_va))                    # fsubr -> rel.y - t*seg.y
            a.raw(b'\xD8\xC8')                                      # ^2
            a.raw(b'\xDE\xC1')                                      # faddp -> d2 (ST1 = t)
            a.raw(b'\xD9\x15' + le32(bed_d2_va))                    # fst d2 (keep)
            a.raw(b'\xD8\x1D' + le32(bed_best_va))                  # fcomp best (pop d2)
            a.raw(b'\xDD\xD8')                                      # fstp st0 (drop t)
            # EAX (= &verts[i], the P pointer) must survive this compare:
            # fnstsw overwrites AX, so every door AFTER the first was measured
            # against a corrupted P — live dump on Torture Chamber showed only
            # ONE bound edge (to door 0, the sole iteration with a valid P)
            # instead of the expected eight, which made the open-field BFS gate
            # nothing and bots ignore door state entirely. pop does not touch
            # EFLAGS, so the jae still sees sahf's comparison bits.
            a.raw(b'\x50')                                          # push eax (save P)
            a.raw(b'\xDF\xE0\x9E')                                  # fnstsw ax; sahf
            a.raw(b'\x58')                                          # pop eax (restore P)
            a.jae('bed_door_next')                                  # d2 >= best
            a.raw(b'\x8B\x0D' + le32(bed_d2_va))                    # best = d2
            a.raw(b'\x89\x0D' + le32(bed_best_va))
            a.raw(b'\x89\xFB')                                      # ebx = edi (best door)
            a.label('bed_door_next')
            a.raw(b'\x47')                                          # ++door
            a.jmp('bed_door_loop')
            a.label('bed_commit')
            a.raw(b'\x83\xFB\xFF')                                  # any door bound?
            a.jz('bed_edge_next')
            a.raw(b'\x89\x1C\xB5' + le32(edge_door_va))             # edge_door[e] = ebx
            # --- Directional per-team pass bits for the bound door --------
            # No authored opener at all -> engine bump-open -> both sides,
            # both teams (0x0F). Otherwise: bits0-1 = team 0 from-i/from-j,
            # bits2-3 = team 1 — a bot-usable opener usable by that team lies
            # on that node's side of the door (sign of dot(o-D, node-D) + 1.0;
            # the bias makes an opener exactly ON the door — self-trigger
            # walk-up doors — grant both sides). Openers only cover walk-in
            # triggers, so a switch/spawn/timer-only door yields 0 = fully
            # blocked while closed (the Torture Chamber pillar walls).
            # EAX = &verts[i], EDX = &verts[j] are still live from the
            # segment math; EBP is a free pushad temp.
            a.raw(b'\xF6\x83' + le32(door_flags_va) + bytes([0x01]))  # test byte [door_flags+ebx], HAS_ANY
            a.jz('bed_pass_both')
            a.raw(b'\x31\xC9')                                      # ecx = pass bits = 0
            a.raw(b'\x31\xFF')                                      # edi = opener idx
            a.label('bed_op_loop')
            a.raw(b'\x3B\x3D' + le32(door_opener_count_va))         # cmp edi, [opener_count]
            a.jae('bed_pass_store')
            a.raw(b'\x89\xFD')                                      # ebp = edi
            a.raw(b'\xC1\xE5\x04')                                  # ebp *= 16 (record stride)
            a.raw(b'\x39\x9D' + le32(door_opener_va + 8))           # opener.door == ebx?
            a.jnz('bed_op_next')
            # s_i = dot(o - D, verts[i] - D) + 1.0 ; sign clear -> i side
            a.raw(b'\xD9\x85' + le32(door_opener_va))               # fld o.x
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va))            # fsub D.x
            a.raw(b'\xD9\x00')                                      # fld [eax] (i.x)
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va))            # fsub D.x
            a.raw(b'\xDE\xC9')                                      # fmulp
            a.raw(b'\xD9\x85' + le32(door_opener_va + 4))           # fld o.y
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va + 4))        # fsub D.y
            a.raw(b'\xD9\x40\x04')                                  # fld [eax+4] (i.y)
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va + 4))        # fsub D.y
            a.raw(b'\xDE\xC9')                                      # fmulp
            a.raw(b'\xDE\xC1')                                      # faddp -> dot
            a.raw(b'\xD9\xE8'); a.raw(b'\xDE\xC1')                  # fld1; faddp (+1.0 bias)
            a.raw(b'\xD9\x1D' + le32(bed_d2_va))                    # fstp s_i
            a.raw(b'\xF7\x05' + le32(bed_d2_va) + le32(0x80000000)) # sign set?
            a.jnz('bed_op_no_i')
            a.raw(b'\xF6\x85' + le32(door_opener_va + 12) + b'\x01')  # opener usable by team0?
            a.jz('bed_op_i_t1')
            a.raw(b'\x83\xC9\x01')                                  # pass |= team0 from-i
            a.label('bed_op_i_t1')
            a.raw(b'\xF6\x85' + le32(door_opener_va + 12) + b'\x02')  # opener usable by team1?
            a.jz('bed_op_no_i')
            a.raw(b'\x83\xC9\x04')                                  # pass |= team1 from-i
            a.label('bed_op_no_i')
            # s_j with verts[j] (EDX)
            a.raw(b'\xD9\x85' + le32(door_opener_va))               # fld o.x
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va))            # fsub D.x
            a.raw(b'\xD9\x02')                                      # fld [edx] (j.x)
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va))            # fsub D.x
            a.raw(b'\xDE\xC9')                                      # fmulp
            a.raw(b'\xD9\x85' + le32(door_opener_va + 4))           # fld o.y
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va + 4))        # fsub D.y
            a.raw(b'\xD9\x42\x04')                                  # fld [edx+4] (j.y)
            a.raw(b'\xD8\x24\xDD' + le32(door_table_va + 4))        # fsub D.y
            a.raw(b'\xDE\xC9')                                      # fmulp
            a.raw(b'\xDE\xC1')                                      # faddp -> dot
            a.raw(b'\xD9\xE8'); a.raw(b'\xDE\xC1')                  # fld1; faddp
            a.raw(b'\xD9\x1D' + le32(bed_d2_va))                    # fstp s_j
            a.raw(b'\xF7\x05' + le32(bed_d2_va) + le32(0x80000000)) # sign set?
            a.jnz('bed_op_no_j')
            a.raw(b'\xF6\x85' + le32(door_opener_va + 12) + b'\x01')  # team0?
            a.jz('bed_op_j_t1')
            a.raw(b'\x83\xC9\x02')                                  # pass |= team0 from-j
            a.label('bed_op_j_t1')
            a.raw(b'\xF6\x85' + le32(door_opener_va + 12) + b'\x02')  # team1?
            a.jz('bed_op_no_j')
            a.raw(b'\x83\xC9\x08')                                  # pass |= team1 from-j
            a.label('bed_op_no_j')
            a.raw(b'\x83\xF9\x0F')                                  # every bit already?
            a.jz('bed_pass_store')
            a.label('bed_op_next')
            a.raw(b'\x47')                                          # ++opener
            a.jmp('bed_op_loop')
            a.label('bed_pass_both')
            a.raw(b'\xB9\x0F\x00\x00\x00')                          # pass = 0x0F
            a.label('bed_pass_store')
            a.raw(b'\x88\x0C\x35' + le32(edge_pass_va))             # edge_pass[e] = cl
            a.label('bed_edge_next')
            a.raw(b'\x46')                                          # ++edge
            a.jmp('bed_edge_loop')
            a.label('bed_done')
            a.raw(b'\x61')                                          # popad
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

    # =====================================================================
    # load_switches: copy the build-time switch centers, class bytes and
    # (switch, door) pair records for the active map into the live tables.
    # Called once per match from detour_df90 right after load_doors (pair
    # door indices reference the same map's door_table order, so both copies
    # must come from the same parse — they do, both static tables pack in
    # parse order). Same bounded map-name match as load_doors. Inert stub
    # when the switch layout fields are absent.
    # =====================================================================
    if not (
        layout.has_field('switch_table')
        and layout.has_field('switch_flags')
        and layout.has_field('switch_pairs')
        and layout.has_field('switch_static_maps')
        and layout.has_field('switch_static_points')
        and layout.has_field('switch_static_flags')
        and layout.has_field('switch_static_pairs')
    ):
        a.label('load_switches')
        a.raw(b'\xC3')
    else:
        switch_count_va         = layout.va('switch_count')
        switch_table_va         = layout.va('switch_table')
        switch_flags_va         = layout.va('switch_flags')
        switch_pair_count_va    = layout.va('switch_pair_count')
        switch_pairs_va         = layout.va('switch_pairs')
        switch_static_map_count_va = layout.va('switch_static_map_count')
        switch_static_maps_va   = layout.va('switch_static_maps')
        switch_static_points_va = layout.va('switch_static_points')
        switch_static_flags_va  = layout.va('switch_static_flags')
        switch_static_pairs_va  = layout.va('switch_static_pairs')
        switch_map_stride       = cfg.SWITCH_MAP_NAME_SLOT + 16

        a.label('load_switches')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(switch_count_va) + le32(0))        # switch_count = 0
        a.raw(b'\xC7\x05' + le32(switch_pair_count_va) + le32(0))   # pair_count = 0
        a.raw(b'\xBF' + le32(switch_table_va))                      # edi = switch_table
        a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX * 2))             # ecx = table dwords
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xFC\xF3\xAB')                                      # cld; rep stosd
        a.raw(b'\xBF' + le32(switch_flags_va))                      # edi = switch_flags
        a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX))                 # ecx = flag bytes
        a.raw(b'\xF3\xAA')                                          # rep stosb (eax still 0)
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = [map CString header]
        a.raw(b'\x85\xC0'); a.jz('lsw_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = map name ASCII
        a.raw(b'\x80\x38\x00'); a.jz('lsw_done')                    # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(switch_static_map_count_va))       # ecx = map_count
        a.raw(b'\x85\xC9'); a.jz('lsw_done')
        a.raw(b'\x83\xF9' + bytes([cfg.SWITCH_STATIC_MAP_MAX]))     # cmp ecx, static max
        a.jbe('lsw_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.SWITCH_STATIC_MAP_MAX))            # cap corrupt count defensively
        a.label('lsw_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('lsw_map_loop')
        a.raw(b'\x39\xCE'); a.jae('lsw_done')                       # if idx >= map_count
        a.raw(b'\x69\xC6' + le32(switch_map_stride))                # eax = idx * map_stride
        a.raw(b'\x05' + le32(switch_static_maps_va))                # eax = &map_entry[idx]
        a.raw(b'\x89\xC7')                                          # edi = entry
        a.raw(b'\x89\xEA')                                          # edx = active map name
        a.raw(b'\x89\xFB')                                          # ebx = entry name

        a.label('lsw_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [entry]
        a.jnz('lsw_next_map')
        a.raw(b'\x84\xC0'); a.jz('lsw_match')                       # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('lsw_str_loop')

        a.label('lsw_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('lsw_map_loop')

        a.label('lsw_match')
        a.raw(b'\x89\xFD')                                          # ebp = map record (name done)
        a.raw(b'\x8B\x4D' + bytes([cfg.SWITCH_MAP_NAME_SLOT]))      # ecx = switch count
        a.raw(b'\x83\xF9' + bytes([cfg.SWITCH_TABLE_MAX]))          # cmp ecx, live cap
        a.jbe('lsw_count_ok')
        a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX))                 # cap live count
        a.label('lsw_count_ok')
        a.raw(b'\x89\x0D' + le32(switch_count_va))                  # switch_count = ecx
        a.raw(b'\x85\xC9'); a.jz('lsw_done')
        a.raw(b'\x8B\x5D' + bytes([cfg.SWITCH_MAP_NAME_SLOT + 4]))  # ebx = first switch idx
        # Points: src = &static_points[first*8], n = count*2 dwords.
        a.raw(b'\x8D\x34\xDD' + le32(switch_static_points_va))      # lea esi, [ebx*8 + points]
        a.raw(b'\xBF' + le32(switch_table_va))                      # edi = live switch_table
        a.raw(b'\xD1\xE1')                                          # ecx *= 2 dwords per point
        a.raw(b'\xFC\xF3\xA5')                                      # cld; rep movsd
        # Flags: src = &static_flags[first], n = switch_count bytes.
        a.raw(b'\x8B\x0D' + le32(switch_count_va))                  # ecx = switch_count
        a.raw(b'\x8D\xB3' + le32(switch_static_flags_va))           # lea esi, [ebx + flags]
        a.raw(b'\xBF' + le32(switch_flags_va))                      # edi = live switch_flags
        a.raw(b'\xF3\xA4')                                          # rep movsb
        # Pairs: count/first from the second record pair. SWITCH_PAIR_MAX
        # (160) exceeds a sign-extended imm8 -> imm32 compare form.
        a.raw(b'\x8B\x4D' + bytes([cfg.SWITCH_MAP_NAME_SLOT + 8]))  # ecx = pair count
        a.raw(b'\x81\xF9' + le32(cfg.SWITCH_PAIR_MAX))              # cmp ecx, live cap
        a.jbe('lsw_pair_count_ok')
        a.raw(b'\xB9' + le32(cfg.SWITCH_PAIR_MAX))                  # cap live count
        a.label('lsw_pair_count_ok')
        a.raw(b'\x89\x0D' + le32(switch_pair_count_va))             # pair_count = ecx
        a.raw(b'\x85\xC9'); a.jz('lsw_done')
        a.raw(b'\x8B\x5D' + bytes([cfg.SWITCH_MAP_NAME_SLOT + 12])) # ebx = first pair idx
        a.raw(b'\x8D\x34\x9D' + le32(switch_static_pairs_va))       # lea esi, [ebx*4 + pairs]
        a.raw(b'\xBF' + le32(switch_pairs_va))                      # edi = live switch_pairs
        a.raw(b'\xF3\xA5')                                          # rep movsd (ecx = count dwords)

        a.label('lsw_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')
