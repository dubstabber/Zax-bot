"""``load_portals`` / ``bind_portal_nodes`` — per-match teleporter-pad
tables (static Data.dat points + routed destinations) and their
nearest-graph-node bindings."""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
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

