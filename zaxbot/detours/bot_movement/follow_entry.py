"""Follow entry: enable gates, door-aware failed-edge fast retry, route
epoch sync and the cold-acquire of the nearest node."""

from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    bot_pos_va = c.bot_pos_va
    bot_slot_tmp_va = c.bot_slot_tmp_va
    current_wp_va = c.current_wp_va
    prev_wp_va = c.prev_wp_va
    wp_try_va = c.wp_try_va
    wp_best_dsq_va = c.wp_best_dsq_va
    failed_edge_va = c.failed_edge_va
    wp_follow_enabled_va = c.wp_follow_enabled_va
    overlay_vertex_count_va = c.overlay_vertex_count_va
    wp_scratch_va = c.wp_scratch_va
    routing = c.routing
    routing_active_va = c.routing_active_va
    route_block_hits_va = c.route_block_hits_va
    door_gate = c.door_gate
    route_block_door_va = c.route_block_door_va
    door_blocked_va = c.door_blocked_va
    door_count_va = c.door_count_va

    # === Waypoint following =============================================
    a.raw(b'\x83\x3D' + le32(wp_follow_enabled_va) + b'\x00')
    a.jz('s542360_fallback_zero')
    a.raw(b'\x83\x3D' + le32(overlay_vertex_count_va) + b'\x00')
    a.jz('s542360_fallback_zero')

    if door_gate:
        # Fast retry: marker set + a door latched + that door now passable
        # -> clear the marker (and the ping-pong budget) so the next arrival
        # retries the edge immediately. A stale latch (map changed under it)
        # only resets the latch; the marker keeps its blind-retry cadence.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
        a.raw(b'\x83\x3C\x8D' + le32(failed_edge_va) + b'\x00')  # marker set?
        a.jz('s542360_door_fc_done')
        a.raw(b'\x8B\x04\x8D' + le32(route_block_door_va))    # eax = latched door idx
        a.raw(b'\x83\xF8\xFF')                                # -1 = none
        a.jz('s542360_door_fc_done')
        a.raw(b'\x3B\x05' + le32(door_count_va))              # stale idx?
        a.jae('s542360_door_fc_stale')
        a.raw(b'\x83\x3C\x85' + le32(door_blocked_va) + b'\x00')  # door still closed?
        a.jnz('s542360_door_fc_done')                         # yes -> keep marker
        a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))   # door opened: retry edge
        if routing:
            a.raw(b'\xC7\x04\x8D' + le32(route_block_hits_va) + le32(0))
        a.label('s542360_door_fc_stale')
        a.raw(b'\xC7\x04\x8D' + le32(route_block_door_va)
              + b'\xFF\xFF\xFF\xFF')                          # drop the latch
        a.label('s542360_door_fc_done')

    # Door-state reroute trigger. rebuild_open_routes bumps route_epoch each
    # time a door open/close rebuilds the open-field BFS. Routing otherwise
    # only re-evaluates on node ARRIVAL (ctf_next_hop fires at s542360_wp_
    # arrived), so a bot steering across a door that opens mid-edge stays
    # committed to the old, now-suboptimal path until it dies and respawns (a
    # bot pressed against a still-closed door never arrives at a node to
    # re-route). When this bot's stored epoch lags the global, sync it and,
    # for a bot NOT latched onto an edge, invalidate current_wp so the
    # cold-acquire below re-runs ctf_next_hop THIS think. An EDGE-LATCHED bot
    # (prev_wp != -1) KEEPS its target: live Battle on the Ice snapshots
    # (2026-07-20) caught the old blanket invalidate snapping bots backward on
    # every rebuild — a self-closing door there flips door_blocked every few
    # seconds, and the Euclidean nearest-node cold-acquire re-latched the node
    # BEHIND a bot that had just crossed the doorway (node 47 sits 30 px on
    # the far side; the 64 px arrival radius then "arrived" it across the
    # closed door and re-planned from the wrong side) — the reported
    # backwards-and-forwards shuttle. A latched bot re-plans against the
    # rebuilt field at its next arrival, and a now-blocked current edge is
    # handled the same think by the closed-door commitment recovery below.
    # Gated on active CTF routing; debounced to at most once per
    # DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES by the rebuild itself.
    if routing and layout.has_field('route_epoch') and layout.has_field('bot_route_epoch'):
        route_epoch_va = layout.va('route_epoch')
        bot_route_epoch_va = layout.va('bot_route_epoch')
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')  # routing active?
        a.jz('s542360_epoch_done')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))             # ecx = slot
        a.raw(b'\xA1' + le32(route_epoch_va))                  # eax = route_epoch
        a.raw(b'\x3B\x04\x8D' + le32(bot_route_epoch_va))      # cmp eax, bot_route_epoch[slot]
        a.jz('s542360_epoch_done')
        a.raw(b'\x89\x04\x8D' + le32(bot_route_epoch_va))      # bot_route_epoch[slot] = epoch
        a.raw(b'\x83\x3C\x8D' + le32(prev_wp_va) + b'\xFF')    # cmp prev_wp[slot], -1
        a.jnz('s542360_epoch_done')                            # edge-latched -> keep target
        a.raw(b'\xC7\x04\x8D' + le32(current_wp_va)
              + b'\xFF\xFF\xFF\xFF')                           # invalidate -> cold re-acquire
        a.label('s542360_epoch_done')

    # Ensure current_wp is a valid index, else cold-acquire the nearest node.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = current_wp[slot]
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_acquire')
    a.raw(b'\x3B\x05' + le32(overlay_vertex_count_va))    # cmp eax, [vertex_count]
    a.jb('s542360_wp_have_cur')                           # valid -> steer
    # fall through: out of range -> acquire

    a.label('s542360_wp_acquire')
    a.raw(b'\xA1' + le32(bot_pos_va))                     # stage bot pos -> wp_scratch
    a.raw(b'\xA3' + le32(wp_scratch_va))
    a.raw(b'\xA1' + le32(bot_pos_va + 4))
    a.raw(b'\xA3' + le32(wp_scratch_va + 4))
    a.call_lbl('wp_find_nearest')                         # ebx = nearest idx or -1
    a.raw(b'\x83\xFB\xFF')                                # cmp ebx, -1
    a.jz('s542360_fallback_zero')                         # empty graph -> idle
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # reload slot
    a.raw(b'\x89\x1C\x8D' + le32(current_wp_va))          # current_wp[slot] = nearest
    a.raw(b'\xC7\x04\x8D' + le32(prev_wp_va) + b'\xFF\xFF\xFF\xFF')   # prev_wp = -1
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    a.raw(b'\xC7\x04\x8D' + le32(failed_edge_va) + le32(0))          # failed_edge_marker = 0
    if door_gate:
        a.raw(b'\xC7\x04\x8D' + le32(route_block_door_va) + b'\xFF\xFF\xFF\xFF')
    # fall through to wp_have_cur

