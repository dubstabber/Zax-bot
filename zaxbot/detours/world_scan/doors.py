"""Door world data: ``load_doors`` (per-match centers + state reset),
``door_capture_wedge`` (latch nearest blocked door on a failed edge),
``door_refresh_state`` (per-frame SOLID-bit readback into
``door_blocked[]``) and ``build_edge_doors`` (per-edge nearest-door +
directional per-team ``edge_pass`` bits)."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_pos_va = layout.va('bot_pos')

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
