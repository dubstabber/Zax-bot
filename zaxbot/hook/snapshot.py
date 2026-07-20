"""``do_snapshot`` body: append one tagged snapshot to ``zax_dump.bin``.

Triggered by R-key from the dispatcher. Each call appends, in order:
  1. ``snap``      — pre-incremented snap_counter (delimits multi-snapshot files)
  2. ``mgr_root``  — *dword_713F14  (0x400 B)
  3. ``session``   — *dword_713F18  (0x200 B)
  4. ``worldmgr``  — *dword_6C2080  (0x400 B)
  5. ``dpmgr``     — captured DP manager (0x1000 B; covers queue + flag at +0x8FC)
  6. ``idx_nbhd``  — 0x6C2900..0x6C2A00 (0x100 B)
  7. ``part[i]``   — 0x118 B for each session participant
  8. ``stats[i]``  — 16 B of *(part+0x1C) for each non-null
  9. ``cstr[i]``   — 16 B of *(*(part+0x1C)) for each non-null
 10. ``charptr``   — 64 B of mgr+0x290's pointer block
 11. ``char[i]``   — 0x200 B for each non-null entry in mgr+0x290 (sanity-checked)

Variable chunks carry the index in the tag (a single ASCII digit at offset +5
or +6 of the tag template). Chunk format is documented in ``zaxbot.config``."""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import AI_PERBOT_FIELD_COUNT, ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    fn_va           = layout.va('fn')
    cap_dpmgr       = layout.va('cap_dpmgr')
    snap_counter_va = layout.va('snap_counter')
    snap_idx_va     = layout.va('snap_idx')
    snap_count_va   = layout.va('snap_count')
    snap_arr_va     = layout.va('snap_arr')
    saved_src_va_va = layout.va('saved_src_va')
    thdr_va         = layout.va('thdr')
    thdr_tag_va     = layout.va('thdr_tag')
    thdr_src_va_va  = layout.va('thdr_src_va')
    thdr_len_va     = layout.va('thdr_len')
    stats_tmp_va    = layout.va('stats_tmp')
    cstr_tmp_va     = layout.va('cstr_tmp')

    tag_snap_marker_va = layout.va('tag_snap_marker')
    tag_mgr_root_va    = layout.va('tag_mgr_root')
    tag_session_va     = layout.va('tag_session')
    tag_worldmgr_va    = layout.va('tag_worldmgr')
    tag_dpmgr_va       = layout.va('tag_dpmgr')
    tag_idx_nbhd_va    = layout.va('tag_idx_nbhd')
    tag_part_va        = layout.va('tag_part')
    tag_stats_va       = layout.va('tag_stats')
    tag_cstr_va        = layout.va('tag_cstr')
    tag_charptr_va     = layout.va('tag_charptr')
    tag_char_va        = layout.va('tag_char')
    tag_ai_fire_va     = layout.va('tag_ai_fire')
    tag_ai_pos_va      = layout.va('tag_ai_pos')
    tag_weapon_info_va = layout.va('tag_weapon_info')
    tag_host_weapon_va = layout.va('tag_host_weapon')
    tag_pc2_weapon_va  = layout.va('tag_pc2_weapon')
    tag_host_wpn_bytes_va = layout.va('tag_host_wpn_bytes')
    tag_pc2_wpn_bytes_va  = layout.va('tag_pc2_wpn_bytes')
    tag_ai_move_va        = layout.va('tag_ai_move')
    tag_hazard_va         = layout.va('tag_hazard')
    tag_wp_diag_va        = layout.va('tag_wp_diag')
    tag_wp_lv_va          = layout.va('tag_wp_lv')
    tag_wp_lay_va         = layout.va('tag_wp_lay')
    tag_wp_map_va         = layout.va('tag_wp_map')
    wp_diag_data_va       = layout.va('wp_diag_data')
    tag_plasma_diag_va    = layout.va('tag_plasma_diag')
    plasma_map_va         = layout.va('plasma_map')
    plasma_diag_va        = layout.va('plasma_diag')
    plasma_qx_va          = layout.va('plasma_qx')
    plasma_qy_va          = layout.va('plasma_qy')
    plasma_tx_va          = layout.va('plasma_tx')
    plasma_ty_va          = layout.va('plasma_ty')
    plasma_grid_va        = layout.va('plasma_grid')
    plasma_cn_count_va    = layout.va('plasma_cn_count')
    plasma_cn_max_va      = layout.va('plasma_cn_max')
    plasma_cn_first_va    = layout.va('plasma_cn_first')
    lava_dbg_heat_va      = layout.va('lava_dbg_heat')
    tag_pheat_va          = layout.va('tag_pheat')
    plasma_heatmap_va     = layout.va('plasma_heatmap')
    plasma_bot_pos_va     = layout.va('bot_pos')
    primary_hash_va    = layout.va('primary_hash')
    host_weapon_obj_va = layout.va('host_weapon_obj')
    host_proto_va_va   = layout.va('host_proto_va')
    host_item_id_va    = layout.va('host_item_id')
    pc2_weapon_obj_va  = layout.va('pc2_weapon_obj')
    pc2_proto_va_va    = layout.va('pc2_proto_va')
    pc2_item_id_va     = layout.va('pc2_item_id')

    # Bot-AI scratch dump regions:
    #   ai_fire: best_target through proj_speed (64 bytes from cand_pos onward;
    #            captures best_dx/dy, best_vx/vy, host_part, proj_speed).
    #   ai_pos:  prev_pos_table + cand_vx/vy (144 bytes from prev_pos_table).
    #   weapon_info: 20 bytes covering the 5 contiguous diagnostic dwords —
    #                current_weapon_obj, current_proto_va,
    #                current_proto_model_va, proto_speed_raw, speed_scale.
    #                Lets the user spot-check def-read correctness
    #                (proto_va non-zero ⇒ projectile weapon;
    #                proto_model_va == 0 ⇒ hitscan; proto_speed_raw in
    #                300..4000 range; speed_scale matches cfg.SPEED_SCALE).
    #                proj_speed itself is already covered by ai_fire.
    ai_fire_src_va     = layout.va('cand_pos')
    ai_pos_src_va      = layout.va('prev_pos_table')
    weapon_info_src_va = layout.va('current_weapon_obj')

    # Bot-movement scratch dump regions:
    #   ai_move: the AI_PERBOT_FIELD_COUNT contiguous parallel u32 arrays × 16
    #            bot slots × 4 bytes. Exposes the wander/diag mirror,
    #            last-position cache, stuck counter, item-scan stagger, pickup
    #            cache, last_damage/flee_ticks, and the waypoint-follow nav
    #            fields (current_wp idx12, prev_wp idx13) per slot. prev_wp !=
    #            0xFFFFFFFF ⇒ the bot is latched onto the graph and following
    #            an edge.
    #   hazard:  hazard_count + hazard_table (4 + 32*12 = 388 bytes). Inspect
    #            after pressing R to verify the proactive scan picked up
    #            damage zones on the current map.
    ai_move_src_va = layout.va('bot_wander_x')
    hazard_src_va  = layout.va('hazard_count')

    a.label('do_snapshot')
    # Open zax_dump.bin (append).
    a.raw(b'\x6A\x00\x68' + le32(0x80) + b'\x6A\x04\x6A\x00\x6A\x03\x68'
          + le32(0x40000000) + b'\x68' + le32(fn_va))
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEFILEA))
    a.raw(b'\x83\xF8\xFF'); a.jz('snap_done')
    a.raw(b'\x85\xC0'); a.jz('snap_done')
    a.raw(b'\x89\xC3')                                        # mov ebx, eax (hFile)
    a.raw(b'\x6A\x02\x6A\x00\x6A\x00\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_SETFILEPTR))

    def emit_chunk(tag_va, ptr_load, length, skip_label,
                   idx_offset=None, idx_var_va=None):
        """Write one tagged chunk: 28-byte header + ``length`` payload bytes.

        ``ptr_load`` is the x86 bytes that load the source pointer into EAX;
        a chunk is skipped when EAX==0. If ``idx_offset`` is given, the tag
        template byte at that offset is overwritten with ('0' + byte at
        idx_var_va) before the header is written. Pre: EBX = hFile. Clobbers
        EAX, ECX, EDX, ESI, EDI.
        """
        a.raw(ptr_load)
        a.raw(b'\x85\xC0'); a.jz(skip_label)
        a.raw(b'\xA3' + le32(saved_src_va_va))
        a.raw(b'\xBE' + le32(tag_va))
        a.raw(b'\xBF' + le32(thdr_tag_va))
        a.raw(b'\xB9\x04\x00\x00\x00')
        a.raw(b'\xFC\xF3\xA5')
        if idx_offset is not None:
            a.raw(b'\xA0' + le32(idx_var_va))
            a.raw(b'\x04' + bytes([ord('0')]))
            a.raw(b'\xA2' + le32(thdr_tag_va + idx_offset))
        a.raw(b'\xA1' + le32(saved_src_va_va))
        a.raw(b'\xA3' + le32(thdr_src_va_va))
        a.raw(b'\xC7\x05' + le32(thdr_len_va) + le32(length))
        a.raw(b'\xB8' + le32(thdr_va))
        a.raw(b'\xBA' + le32(cfg.DUMP_HEADER_SIZE))
        a.call_lbl('wbuf')
        a.raw(b'\xA1' + le32(saved_src_va_va))
        a.raw(b'\xBA' + le32(length))
        a.call_lbl('wbuf')
        a.label(skip_label)

    # 1. snap marker
    a.raw(b'\xFF\x05' + le32(snap_counter_va))                # inc [snap_counter]
    emit_chunk(tag_snap_marker_va, b'\xB8' + le32(snap_counter_va), 4, 'snap_skip_marker')

    # 2-6. Fixed-region chunks.
    emit_chunk(tag_mgr_root_va,  b'\xA1' + le32(ax.MANAGER_GLOBAL_VA), 0x400, 'snap_skip_mgr')
    emit_chunk(tag_session_va,   b'\xA1' + le32(ax.SESSION_GLOBAL),    0x200, 'snap_skip_session')
    emit_chunk(tag_worldmgr_va,  b'\xA1' + le32(ax.WORLDMGR_GLOBAL),   0x400, 'snap_skip_wm')
    emit_chunk(tag_dpmgr_va,     b'\xA1' + le32(cap_dpmgr),           0x1000, 'snap_skip_dp')
    emit_chunk(tag_idx_nbhd_va,  b'\xB8\x00\x29\x6C\x00',              0x100, 'snap_skip_idx')

    # Bot-AI scratch dumps for shooting-prediction debugging.
    emit_chunk(tag_ai_fire_va,   b'\xB8' + le32(ai_fire_src_va),         0x40, 'snap_skip_ai_fire')
    emit_chunk(tag_ai_pos_va,    b'\xB8' + le32(ai_pos_src_va),         0x100, 'snap_skip_ai_pos')
    emit_chunk(tag_weapon_info_va, b'\xB8' + le32(weapon_info_src_va),    0x14, 'snap_skip_weapon_info')

    # Bot-movement scratch dumps. ai_move covers AI_PERBOT_FIELD_COUNT per-bot
    # fields × 16 slots × 4 bytes (= 15 × 16 × 4 = 960 = 0x3C0 today): wander
    # state + stuck + item attractor cache + last_damage + flee_ticks + the
    # waypoint-follow nav fields: current_wp idx12 (off 0x300), prev_wp idx13
    # (off 0x340), wp_try idx14 (off 0x380, frames since last node arrival).
    # prev_wp != 0xFFFFFFFF ⇒ the bot has latched onto the graph and is
    # following an edge. (bot_route_suspend lives in the flag-route block, not
    # here.) Size is derived from the layout constant so it tracks the field
    # list automatically. hazard covers hazard_count + hazard_table.
    ai_move_dump_bytes = AI_PERBOT_FIELD_COUNT * cfg.MAX_BOT_SLOTS * 4
    emit_chunk(tag_ai_move_va,   b'\xB8' + le32(ai_move_src_va),  ai_move_dump_bytes, 'snap_skip_ai_move')
    emit_chunk(tag_hazard_va,    b'\xB8' + le32(hazard_src_va),         0x190, 'snap_skip_hazard')

    # --- Door-state diagnostics (live-reroute debugging). One R press pins
    # the whole chain: door_cnt proves load_doors matched the map (count +
    # match/wedge radii); door_blk is the live open/closed readback the
    # overlay rings and the open-field BFS consume; door_ent shows whether
    # the periodic grid walk actually cached entities at each anchor (all
    # zeros = position-match failure, the readback then never flips);
    # door_dyn covers door_dirty/rebuild_cd/route_use_open/bfs spills
    # (route_use_open = which field ctf_next_hop last scanned); edge_door /
    # edge_pass pin the static per-match edge->door binding + directional
    # pass bits. Prefixes: 48 doors / 64 edges — covers every shipped map's
    # graph and all but Curse of the Temple's 186-door tail (Torture = 43).
    if (layout.has_field('tag_door_cnt') and layout.has_field('door_count')
            and layout.has_field('door_blocked')
            and layout.has_field('door_entity')
            and layout.has_field('door_dirty')):
        emit_chunk(layout.va('tag_door_cnt'),
                   b'\xB8' + le32(layout.va('door_count')), 0x24, 'snap_skip_door_cnt')
        emit_chunk(layout.va('tag_door_blk'),
                   b'\xB8' + le32(layout.va('door_blocked')), 0xC0, 'snap_skip_door_blk')
        emit_chunk(layout.va('tag_door_ent'),
                   b'\xB8' + le32(layout.va('door_entity')), 0x240, 'snap_skip_door_ent')
        emit_chunk(layout.va('tag_door_dyn'),
                   b'\xB8' + le32(layout.va('door_dirty')), 0x18, 'snap_skip_door_dyn')
    if layout.has_field('tag_edge_door') and layout.has_field('edge_door'):
        emit_chunk(layout.va('tag_edge_door'),
                   b'\xB8' + le32(layout.va('edge_door')), 0x100, 'snap_skip_edge_door')
    if layout.has_field('tag_edge_pass') and layout.has_field('edge_pass'):
        emit_chunk(layout.va('tag_edge_pass'),
                   b'\xB8' + le32(layout.va('edge_pass')), 0x40, 'snap_skip_edge_pass')
    # Routing-decision state: dump the whole flag-route block (globals +
    # per-bot missing-policy/goal/suspend/block-hits + route_epoch +
    # bot_route_epoch) in one contiguous chunk from flag_routing_active.
    if layout.has_field('tag_rstate') and layout.has_field('flag_routing_active'):
        emit_chunk(layout.va('tag_rstate'),
                   b'\xB8' + le32(layout.va('flag_routing_active')), 0x170, 'snap_skip_rstate')
    # Switch detection: one contiguous chunk covering the whole live block
    # (switch_count | overlay color | pair_count | switch_table | switch_pairs
    # | switch_flags) — pins load_switches' map match, the copied centers,
    # the (switch, door) pair bindings and the class bytes in one R press.
    if layout.has_field('tag_switch') and layout.has_field('switch_count'):
        switch_dump_len = (layout.field('switch_flags').offset
                           + layout.field('switch_flags').size
                           - layout.field('switch_count').offset)
        emit_chunk(layout.va('tag_switch'),
                   b'\xB8' + le32(layout.va('switch_count')),
                   switch_dump_len, 'snap_skip_switch')
    # Switch-seek state: switch_node bindings + the whole per-team block
    # (active/node/pending/req/tried/fail/timer/best) + bot_seek — one R
    # press pins which switch a team is seeking, why (req node/goal), and
    # which bots are participating. seek_dist is omitted (2KB of field).
    if layout.has_field('tag_seek') and layout.has_field('switch_node'):
        seek_dump_len = (layout.field('bot_seek').offset
                         + layout.field('bot_seek').size
                         - layout.field('switch_node').offset)
        emit_chunk(layout.va('tag_seek'),
                   b'\xB8' + le32(layout.va('switch_node')),
                   seek_dump_len, 'snap_skip_seek')
    # Portal-routing state: one contiguous chunk from portal_dest_table
    # through pw_spill (dest coords, has-dest flags, pad/dest node bindings,
    # per-bot pad latches, next-hop spill, wander/jump knobs, last jump d²) —
    # one R press pins whether bind_portal_nodes bound the pads, which bots
    # are pad-latched, and whether the jump detector saw the teleport.
    if layout.has_field('tag_proute') and layout.has_field('portal_dest_table'):
        proute_dump_len = (layout.field('pw_spill').offset
                           + layout.field('pw_spill').size
                           - layout.field('portal_dest_table').offset)
        emit_chunk(layout.va('tag_proute'),
                   b'\xB8' + le32(layout.va('portal_dest_table')),
                   proute_dump_len, 'snap_skip_proute')
    # Dropped-flag pursuit state: one contiguous chunk from flag_drop_valid
    # through drop_pursue_enabled (per-flag drop valid/position, per-bot
    # pursuit latch + cooldown, the radius/reached/enabled knobs) — one R
    # press pins whether the scan sees a dropped copy and who is pursuing.
    if layout.has_field('tag_dpursuit') and layout.has_field('flag_drop_valid'):
        dpursuit_dump_len = (layout.field('drop_pursue_enabled').offset
                             + layout.field('drop_pursue_enabled').size
                             - layout.field('flag_drop_valid').offset)
        emit_chunk(layout.va('tag_dpursuit'),
                   b'\xB8' + le32(layout.va('flag_drop_valid')),
                   dpursuit_dump_len, 'snap_skip_dpursuit')
    # Roam switch wander-bump state: one contiguous chunk from
    # bot_switch_target through sww_census (per-bot bump latch, re-roll
    # cooldown, press patience, latch-time census + the chance knob and
    # spills) — one R press pins which bots are pressing which switch and
    # why a roll did or did not fire.
    if layout.has_field('tag_swander') and layout.has_field('bot_switch_target'):
        swander_dump_len = (layout.field('sww_census').offset
                            + layout.field('sww_census').size
                            - layout.field('bot_switch_target').offset)
        emit_chunk(layout.va('tag_swander'),
                   b'\xB8' + le32(layout.va('bot_switch_target')),
                   swander_dump_len, 'snap_skip_swander')
    # Salvage King state: one contiguous chunk from sk_routing_active through
    # sk_pile_pos (live mineral/bin tables + node binds, per-bot phase/carry/
    # deposit-patience latches, pile-divert latches and the pile ring) — one
    # R press pins the whole SK decision state; the static pack and the BFS
    # rows after tag_skstate are excluded (like seek_dist / drop_dist).
    if layout.has_field('tag_skstate') and layout.has_field('sk_routing_active'):
        skstate_dump_len = (layout.field('sk_pile_pos').offset
                            + layout.field('sk_pile_pos').size
                            - layout.field('sk_routing_active').offset)
        emit_chunk(layout.va('tag_skstate'),
                   b'\xB8' + le32(layout.va('sk_routing_active')),
                   skstate_dump_len, 'snap_skip_skstate')
    # Wedge-escape + fight-stall state: the wp_find_nearest_ex exclusion list
    # (which nodes the last hard reset ruled out), per-bot wedge-cycle
    # counters, and the per-bot enemy-near stamps — one R press pins whether
    # a milling bot is accruing toward a hard reset and whether the movement
    # watchdog currently sees its stall as a fight.
    if layout.has_field('tag_wedge') and layout.has_field('wpfn_excl'):
        wedge_dump_len = (layout.field('bot_enemy_near').offset
                          + layout.field('bot_enemy_near').size
                          - layout.field('wpfn_excl').offset)
        emit_chunk(layout.va('tag_wedge'),
                   b'\xB8' + le32(layout.va('wpfn_excl')),
                   wedge_dump_len, 'snap_skip_wedge')
    # Goody pursuit state: item field gate/count, the per-think resolved
    # target (tx/ty/node/idx), scan inputs, radius knobs, pile dirty flag +
    # node binds — one R press pins what a latched bot is chasing and why.
    # The static pack + BFS fields after tag_goody are excluded.
    if layout.has_field('tag_goody') and layout.has_field('item_routing_active'):
        goody_end = (layout.field('sk_pile_node')
                     if layout.has_field('sk_pile_node')
                     else layout.field('goody_abandon_radius_sq'))
        goody_dump_len = (goody_end.offset + goody_end.size
                          - layout.field('item_routing_active').offset)
        emit_chunk(layout.va('tag_goody'),
                   b'\xB8' + le32(layout.va('item_routing_active')),
                   goody_dump_len, 'snap_skip_goody')

    # Waypoint-graph probe. wp_compute populates wp_diag_data[0..7]:
    #   [+0x00] MGR, [+0x04] WM, [+0x08] LV, [+0x0C] WPM,
    #   [+0x10] char count, [+0x14] layer_arr, [+0x18] LAY, [+0x1C] WPM_REAL.
    # `wp_lv`  dumps 0x200B from LV  (vtbl[0x184] object, expected non-CLayer)
    # `wp_lay` dumps 0x200B from LAY (active CLayer; expect [+0x134] to be a
    #          heap ptr to a CWayPointMap with vtable 0x5FC760).
    a.call_lbl('wp_compute')
    emit_chunk(tag_wp_diag_va, b'\xB8' + le32(wp_diag_data_va),         0x20,  'snap_skip_wp_diag')
    emit_chunk(tag_wp_lv_va,   b'\xA1' + le32(wp_diag_data_va + 0x08),  0x200, 'snap_skip_wp_lv')
    emit_chunk(tag_wp_lay_va,  b'\xA1' + le32(wp_diag_data_va + 0x18),  0x200, 'snap_skip_wp_lay')
    # CWayPointMap is 52 bytes (vtable + two embedded 16-byte CLists +
    # MinDist/MaxDist/extra ints). Dump 0x40 for a bit of padding so we
    # can confirm the vtable matches 0x5FC760 and read the two CList
    # counts to learn whether the map has any polygons/nodes.
    emit_chunk(tag_wp_map_va,  b'\xA1' + le32(wp_diag_data_va + 0x1C),  0x40,  'snap_skip_wp_map')

    # --- Plasma (lava) detection pin. scan_plasma (run per match by detour_df90)
    # has already filled plasma_diag[0..6] with LAY, both raw candidate pointers,
    # the vtable-validated plasma map, and tilepx/tw/th. Here we additionally
    # sample the static footprint at the HOST's position via is_plasma_at — this
    # exercises the engine grid getter (the M2 hot-path call) in the safe R-key
    # context and records host_x/y, host_tx/ty and the footprint byte into
    # plasma_diag[7..11]. Stand the host on lava and press R: diag[11] should be
    # nonzero on lava and zero on safe ground; diag[3] (chosen) tells which layer
    # field (diag[1]=+0x7C vs diag[2]=+0x40) held the real CPlasmaTileMap.
    # ebx (= hFile) is preserved: is_plasma_at clobbers only eax/ecx/edx and the
    # engine getters preserve ebx.
    a.raw(b'\x83\x3D' + le32(plasma_map_va) + b'\x00')        # cmp [plasma_map], 0
    a.jz('snap_plasma_emit')
    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                 # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('snap_plasma_emit')
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                        # eax = [eax+0x290] charArray
    a.raw(b'\x85\xC0'); a.jz('snap_plasma_emit')
    a.raw(b'\x8B\x08')                                        # ecx = charArray[0] (host)
    a.raw(b'\x85\xC9'); a.jz('snap_plasma_emit')
    a.raw(b'\x81\xF9\x00\x00\x10\x00')                        # cmp ecx, 0x100000 (heap)
    a.jb('snap_plasma_emit')
    a.raw(b'\x68' + le32(plasma_bot_pos_va))                  # push &bot_pos
    a.call_va(ax.SUB_4FB0A0_VA)                               # __thiscall, ret 4 (writes float x,y)
    # float pos -> int world coords (round-to-nearest is fine for the pin).
    a.raw(b'\xD9\x05' + le32(plasma_bot_pos_va))              # fld  [bot_pos.x]
    a.raw(b'\xDB\x1D' + le32(plasma_qx_va))                   # fistp [plasma_qx]
    a.raw(b'\xD9\x05' + le32(plasma_bot_pos_va + 4))          # fld  [bot_pos.y]
    a.raw(b'\xDB\x1D' + le32(plasma_qy_va))                   # fistp [plasma_qy]
    a.raw(b'\xA1' + le32(plasma_qx_va))                       # diag[7] = host_x
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x1C))
    a.raw(b'\xA1' + le32(plasma_qy_va))                       # diag[8] = host_y
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x20))
    a.call_lbl('is_plasma_at')                                # eax = 0/1; sets plasma_tx/ty
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x2C))              # diag[11] = is_plasma_at@host
    a.raw(b'\xA1' + le32(lava_dbg_heat_va))                  # diag[19] = heat is_plasma_at read (post-warm)
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x4C))
    a.raw(b'\xA1' + le32(plasma_tx_va))                       # diag[9] = host_tx
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x24))
    a.raw(b'\xA1' + le32(plasma_ty_va))                       # diag[10] = host_ty
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x28))
    # heat@host: same host tile (plasma_tx/ty still set by is_plasma_at), heat grid.
    a.raw(b'\xA1' + le32(plasma_map_va))                      # eax = plasma_map
    a.raw(b'\x05' + le32(ax.CPLASMA_HEAT_OFF))                # add eax, 0x2C6C (heat grid)
    a.raw(b'\xA3' + le32(plasma_grid_va))
    a.call_lbl('plasma_get')                                  # eax = heat@host tile
    a.raw(b'\xA3' + le32(plasma_diag_va + 0x30))              # diag[12] = heat@host
    # Whole-grid census of the FOOTPRINT grid (plasma+0x08).
    a.raw(b'\xA1' + le32(plasma_map_va))
    a.raw(b'\x83\xC0' + bytes([ax.CPLASMA_FOOTPRINT_OFF]))    # add eax, 8
    a.raw(b'\xA3' + le32(plasma_grid_va))
    a.call_lbl('plasma_census')
    a.raw(b'\xA1' + le32(plasma_cn_count_va)); a.raw(b'\xA3' + le32(plasma_diag_va + 0x34))  # [13] fp_count
    a.raw(b'\xA1' + le32(plasma_cn_max_va));   a.raw(b'\xA3' + le32(plasma_diag_va + 0x3C))  # [15] fp_max
    a.raw(b'\xA1' + le32(plasma_cn_first_va)); a.raw(b'\xA3' + le32(plasma_diag_va + 0x44))  # [17] fp_first
    # Whole-grid census of the HEAT/Elevation grid (plasma+0x2C6C).
    a.raw(b'\xA1' + le32(plasma_map_va))
    a.raw(b'\x05' + le32(ax.CPLASMA_HEAT_OFF))               # add eax, 0x2C6C
    a.raw(b'\xA3' + le32(plasma_grid_va))
    a.call_lbl('plasma_census')
    a.raw(b'\xA1' + le32(plasma_cn_count_va)); a.raw(b'\xA3' + le32(plasma_diag_va + 0x38))  # [14] heat_count
    a.raw(b'\xA1' + le32(plasma_cn_max_va));   a.raw(b'\xA3' + le32(plasma_diag_va + 0x40))  # [16] heat_max
    a.raw(b'\xA1' + le32(plasma_cn_first_va)); a.raw(b'\xA3' + le32(plasma_diag_va + 0x48))  # [18] heat_first
    a.call_lbl('plasma_dump_heat')                            # fill plasma_heatmap (every tile)
    a.label('snap_plasma_emit')
    emit_chunk(tag_plasma_diag_va, b'\xB8' + le32(plasma_diag_va), 0x50, 'snap_skip_plasma_diag')
    emit_chunk(tag_pheat_va, b'\xB8' + le32(plasma_heatmap_va), 0x800, 'snap_skip_pheat')

    # --- Host-side weapon lookup (diagnostic). Resolves the host's currently
    # equipped Primary weapon and stashes (item_id, weapon_obj, item_def) so
    # the user can discover valid item ids by picking up weapons in-game and
    # pressing R. Mirrors compute_proj_speed's chain but on charArray[0]
    # instead of the bot. Safe because R is pressed long after the host is
    # fully initialised.
    a.raw(b'\xC7\x05' + le32(host_weapon_obj_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(host_proto_va_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(host_item_id_va) + le32(0xFFFFFFFF))
    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                 # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                        # eax = [eax+0x290] (charArray)
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\x8B\x08')                                        # ecx = charArray[0] (host char)
    a.raw(b'\x85\xC9'); a.jz('snap_skip_host_wpn')
    # Range-check the char ptr before calling sub_4267E0 (`mov eax,[ecx];
    # jmp [eax+0x90]`). On lava maps the host slot occasionally holds a
    # small sentinel (observed: 4) rather than a heap pointer, which
    # page-faults `mov eax,[ecx]`. Char pointers are heap-allocated, so
    # require >= 0x100000 (above the static .data area).
    a.raw(b'\x81\xF9\x00\x00\x10\x00')                        # cmp ecx, 0x100000
    a.jb('snap_skip_host_wpn')
    a.call_va(ax.SUB_4267E0_VA)                               # eax = inv
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\x89\xC6')                                        # esi = inv
    # Lazy-init primary_hash (compute_proj_speed normally warms this; on a
    # very early R-press it could still be 0).
    a.raw(b'\x83\x3D' + le32(primary_hash_va) + b'\x00')
    a.jnz('snap_host_have_hash')
    a.raw(b'\x6A\xFF')                                        # push -1
    a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\xA3' + le32(primary_hash_va))
    a.label('snap_host_have_hash')
    # sub_425290(this=inv, hash) -> item_id
    a.raw(b'\xFF\x35' + le32(primary_hash_va))                # push [primary_hash]
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.call_va(ax.SUB_425290_VA)
    a.raw(b'\xA3' + le32(host_item_id_va))                    # [host_item_id] = eax
    # inv.vtable[+0x68](inv, item_id) -> weapon obj
    a.raw(b'\x50')                                            # push eax (item_id)
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.raw(b'\x8B\x01')                                        # eax = [ecx] (vtable)
    a.raw(b'\xFF\x50' + bytes([ax.INVENTORY_GET_WEAPON_OFF])) # call [eax+0x68]
    a.raw(b'\x85\xC0'); a.jz('snap_skip_host_wpn')
    a.raw(b'\xA3' + le32(host_weapon_obj_va))
    a.raw(b'\x8B\xC8')                                        # ecx = weapon obj
    a.call_va(ax.SUB_4DD480_VA)                               # eax = item definition
    a.raw(b'\xA3' + le32(host_proto_va_va))
    a.label('snap_skip_host_wpn')

    emit_chunk(tag_host_weapon_va, b'\xB8' + le32(host_weapon_obj_va), 0x0C, 'snap_skip_host_weapon_dump')

    # --- PC2-side weapon lookup (diagnostic). Same chain as the host block.
    # Prefer charArray[2] so a host+bot+PC2 session samples the real remote
    # client (charArray[1] is the first bot there); fall back to charArray[1]
    # for host+PC2 sessions without an earlier bot.
    a.raw(b'\xC7\x05' + le32(pc2_weapon_obj_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(pc2_proto_va_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(pc2_item_id_va) + le32(0xFFFFFFFF))
    a.raw(b'\xA1' + le32(ax.WORLDMGR_GLOBAL))                 # eax = worldmgr
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                        # eax = [eax+0x290] (charArray)
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\x8B\x48\x08')                                    # ecx = charArray[2] (PC2 after bot)
    a.raw(b'\x85\xC9')                                        # test ecx, ecx
    a.jnz('snap_pc2_have_char')
    a.raw(b'\x8B\x48\x04')                                    # fallback: charArray[1]
    a.label('snap_pc2_have_char')
    a.raw(b'\x85\xC9'); a.jz('snap_skip_pc2_wpn')
    # Same range check as host_weapon — see note above. The PC2 slot is
    # the actual crash-trigger on the lava map (charArray[2] = 4 sentinel
    # caused #PF on sub_4267E0's `mov eax, [ecx]`).
    a.raw(b'\x81\xF9\x00\x00\x10\x00')                        # cmp ecx, 0x100000
    a.jb('snap_skip_pc2_wpn')
    a.call_va(ax.SUB_4267E0_VA)                               # eax = inv
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\x89\xC6')                                        # esi = inv
    # primary_hash is shared (process-wide); should already be warmed by the
    # host block above or compute_proj_speed.
    a.raw(b'\x83\x3D' + le32(primary_hash_va) + b'\x00')
    a.jnz('snap_pc2_have_hash')
    a.raw(b'\x6A\xFF')
    a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\xA3' + le32(primary_hash_va))
    a.label('snap_pc2_have_hash')
    a.raw(b'\xFF\x35' + le32(primary_hash_va))                # push [primary_hash]
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.call_va(ax.SUB_425290_VA)
    a.raw(b'\xA3' + le32(pc2_item_id_va))                     # [pc2_item_id] = eax
    a.raw(b'\x50')                                            # push eax (item_id)
    a.raw(b'\x8B\xCE')                                        # ecx = inv
    a.raw(b'\x8B\x01')                                        # eax = [ecx] (vtable)
    a.raw(b'\xFF\x50' + bytes([ax.INVENTORY_GET_WEAPON_OFF])) # call [eax+0x68]
    a.raw(b'\x85\xC0'); a.jz('snap_skip_pc2_wpn')
    a.raw(b'\xA3' + le32(pc2_weapon_obj_va))
    a.raw(b'\x8B\xC8')                                        # ecx = weapon obj
    a.call_va(ax.SUB_4DD480_VA)                               # eax = item definition
    a.raw(b'\xA3' + le32(pc2_proto_va_va))
    a.label('snap_skip_pc2_wpn')

    emit_chunk(tag_pc2_weapon_va, b'\xB8' + le32(pc2_weapon_obj_va), 0x0C, 'snap_skip_pc2_weapon_dump')

    # --- Raw 128-byte dumps of each weapon object for layout comparison.
    # `ptr_load` here uses `mov eax, [scratch]` (opcode A1) so
    # the source is the weapon-obj pointer stored at host/pc2_weapon_obj.
    emit_chunk(tag_host_wpn_bytes_va, b'\xA1' + le32(host_weapon_obj_va), 0x80, 'snap_skip_host_wpn_b')
    emit_chunk(tag_pc2_wpn_bytes_va,  b'\xA1' + le32(pc2_weapon_obj_va),  0x80, 'snap_skip_pc2_wpn_b')

    # 7-9. Per-participant iteration: dpmgr.array[0..count). Entries are direct
    # 280B participant pointers — no -0x3C sink dance.
    a.raw(b'\xA1' + le32(cap_dpmgr))                          # mov eax, [cap_dpmgr]
    a.raw(b'\x85\xC0'); a.jz('snap_skip_parts')
    a.raw(b'\x8B\x48\x18')                                    # mov ecx, [eax+0x18]  (count)
    a.raw(b'\x83\xF9\x10'); a.jb('snap_part_count_ok')        # cap to 16
    a.raw(b'\xB9\x10\x00\x00\x00')
    a.label('snap_part_count_ok')
    a.raw(b'\x8B\x40\x14')                                    # mov eax, [eax+0x14]  (array)
    a.raw(b'\x85\xC0'); a.jz('snap_skip_parts')
    a.raw(b'\x89\x0D' + le32(snap_count_va))
    a.raw(b'\xA3' + le32(snap_arr_va))
    a.raw(b'\xC7\x05' + le32(snap_idx_va) + le32(0))

    a.label('snap_part_loop')
    a.raw(b'\xA1' + le32(snap_idx_va))
    a.raw(b'\x3B\x05' + le32(snap_count_va))
    a.jae('snap_skip_parts')
    a.raw(b'\x8B\x15' + le32(snap_arr_va))
    a.raw(b'\x8B\x14\x82')                                    # edx = arr[eax] (participant)
    a.raw(b'\x85\xD2'); a.jz('snap_part_skip')
    emit_chunk(tag_part_va, b'\x89\xD0', 0x118, 'snap_part_wrote',
               idx_offset=5, idx_var_va=snap_idx_va)
    # Bot-name probe: stage *(part+0x1C) and *(*(part+0x1C)) into tmps.
    a.raw(b'\xC7\x05' + le32(stats_tmp_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(cstr_tmp_va) + le32(0))
    a.raw(b'\xA1' + le32(snap_idx_va))
    a.raw(b'\x8B\x15' + le32(snap_arr_va))
    a.raw(b'\x8B\x14\x82')                                    # re-derive participant (emit_chunk clobbered edx)
    a.raw(b'\x85\xD2'); a.jz('snap_probe_emit')
    a.raw(b'\x8B\x42\x1C')                                    # eax = stats sub-object
    a.raw(b'\x85\xC0'); a.jz('snap_probe_emit')
    a.raw(b'\xA3' + le32(stats_tmp_va))
    a.raw(b'\x8B\x00')                                        # eax = CString header
    a.raw(b'\x85\xC0'); a.jz('snap_probe_emit')
    a.raw(b'\xA3' + le32(cstr_tmp_va))
    a.label('snap_probe_emit')
    emit_chunk(tag_stats_va, b'\xA1' + le32(stats_tmp_va), 16, 'snap_stats_wrote',
               idx_offset=6, idx_var_va=snap_idx_va)
    emit_chunk(tag_cstr_va,  b'\xA1' + le32(cstr_tmp_va),  16, 'snap_cstr_wrote',
               idx_offset=5, idx_var_va=snap_idx_va)
    a.label('snap_part_skip')
    a.raw(b'\xFF\x05' + le32(snap_idx_va))                    # inc [snap_idx]
    a.jmp('snap_part_loop')
    a.label('snap_skip_parts')

    # 10-11. Per-character iteration: scan mgr+0x290[0..16] with pointer sanity.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))
    a.raw(b'\x85\xC0'); a.jz('snap_skip_chars')
    a.raw(b'\x8B\x90' + le32(0x290))                          # edx = char_arr
    a.raw(b'\x85\xD2'); a.jz('snap_skip_chars')
    a.raw(b'\x89\x15' + le32(snap_arr_va))                    # save char_arr for char loop
    emit_chunk(tag_charptr_va, b'\xA1' + le32(snap_arr_va), 64, 'snap_skip_charptr')
    a.raw(b'\xC7\x05' + le32(snap_count_va) + le32(16))
    a.raw(b'\xC7\x05' + le32(snap_idx_va) + le32(0))

    a.label('snap_char_loop')
    a.raw(b'\xA1' + le32(snap_idx_va))
    a.raw(b'\x3B\x05' + le32(snap_count_va))
    a.jae('snap_skip_chars')
    a.raw(b'\x8B\x15' + le32(snap_arr_va))
    a.raw(b'\x8B\x14\x82')
    a.raw(b'\x81\xFA\x00\x00\x40\x00')                        # cmp edx, 0x400000 (image base)
    a.jb('snap_char_skip')
    a.raw(b'\x81\xFA\x00\x00\x00\x70')
    a.jae('snap_char_skip')
    emit_chunk(tag_char_va, b'\x89\xD0', 0x200, 'snap_char_wrote',
               idx_offset=5, idx_var_va=snap_idx_va)
    a.label('snap_char_skip')
    a.raw(b'\xFF\x05' + le32(snap_idx_va))
    a.jmp('snap_char_loop')
    a.label('snap_skip_chars')

    a.raw(b'\x53'); a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))
    a.label('snap_done')
    a.raw(b'\xC3')
