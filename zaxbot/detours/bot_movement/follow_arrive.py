"""Node arrival: wedge-counter reset, advance via routed next-hop
(ctf/drop/sk dispatch, portal pad hop latch) or the random
``wp_advance`` fallback with portal/switch wander rolls."""

from ... import config as cfg
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
    overlay_vertex_count_va = c.overlay_vertex_count_va
    overlay_vertices_va = c.overlay_vertices_va
    routing = c.routing
    route_suspend_va = c.route_suspend_va
    route_block_hits_va = c.route_block_hits_va
    door_gate = c.door_gate
    route_block_door_va = c.route_block_door_va
    door_gate_table_va = c.door_gate_table_va
    door_wedge_radius_sq_va = c.door_wedge_radius_sq_va
    door_blocked_va = c.door_blocked_va
    door_count_va = c.door_count_va
    wedge_reset = c.wedge_reset
    bot_wedge_cycles_va = c.bot_wedge_cycles_va
    portal_move = c.portal_move
    bot_portal_target_va = c.bot_portal_target_va
    route_portal_hop_va = c.route_portal_hop_va
    drop_move = c.drop_move
    bot_drop_target_va = c.bot_drop_target_va
    chase_move = c.chase_move
    bot_chase_flag_va = c.bot_chase_flag_va
    switch_wander = c.switch_wander
    bot_switch_target_va = c.bot_switch_target_va
    bot_switch_cd_va = c.bot_switch_cd_va
    bot_switch_try_va = c.bot_switch_try_va
    bot_switch_snap_va = c.bot_switch_snap_va
    sww_census_va = c.sww_census_va
    sk_move = c.sk_move
    sk_active_mv_va = c.sk_active_mv_va
    bot_pile_target_va = c.bot_pile_target_va
    goody_move = c.goody_move
    item_active_mv_va = c.item_active_mv_va

    a.label('s542360_wp_arrived')
    if wedge_reset:
        # A NORMAL-radius node arrival = real progress; the wedge counter
        # resets. STUCK-radius arrivals enter at s542360_wp_arrived_gate
        # below and deliberately SKIP this reset: the 128px stuck ball pokes
        # through walls (live 2026-07-20, Battle on the Ice: a blue carrier
        # north of the closed south team door "arrived" at inside node 78
        # from 123px away, and every such fake arrival zeroed the counter,
        # so the wedge HARD RESET that would have rescued it to the north-
        # side node never fired).
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\xC7\x04\x8D' + le32(bot_wedge_cycles_va) + le32(0))
    if door_gate and layout.has_field('bot_door_patience'):
        # Real progress also refills the door press-patience budget: the
        # node-gate latch path now exercises patience on every routed
        # timeout at a door-adjacent target, so a stale count from an
        # earlier (successful) press must not starve the next door.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\xC7\x04\x8D' + le32(layout.va('bot_door_patience')) + le32(0))
    a.label('s542360_wp_arrived_gate')
    if door_gate:
        # --- Door-side ARRIVAL gate (live 2026-07-20, Battle on the Ice) ---
        # Node 47 sits 30px behind the south team door, so the 64px arrival
        # ball (128px stuck) claims "arrived" while the bot is still OUTSIDE
        # the closed door. The next hop then targets an inside node and the
        # straight line from the bot's REAL position crosses the wall west
        # of the doorway — the reported carrier wall-grind. Refuse any
        # arrival at a node within the door-wedge radius of a currently-
        # BLOCKED door when the bot is on the far side of that door
        # (dot(bot - door, node - door) < 0): fall into the no-progress
        # path instead, so the bot keeps steering at the node — i.e. INTO
        # the door, which is exactly what fires its walk-up opener — and
        # the watchdog/door-press-patience machinery stays armed. Once the
        # door reads open (per-frame refresh) the gate is inert and the
        # arrival goes through.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))      # eax = arrived node
        a.raw(b'\x3B\x05' + le32(overlay_vertex_count_va))
        a.jae('s542360_wp_ag_ok')                         # bad idx (-1 = huge)
        a.raw(b'\x8D\x34\xC5' + le32(overlay_vertices_va))  # esi = &verts[node]
        a.raw(b'\x31\xD2')                                # edx = 0 (door idx)
        a.label('s542360_wp_ag_loop')
        a.raw(b'\x3B\x15' + le32(door_count_va))          # all doors checked?
        a.jae('s542360_wp_ag_ok')
        a.raw(b'\x83\x3C\x95' + le32(door_blocked_va) + b'\x00')
        a.jz('s542360_wp_ag_next')                        # open door -> skip
        a.raw(b'\x8D\x3C\xD5' + le32(door_gate_table_va))  # edi = &door_table[d]
        # t = node - door; d2 = t.x^2 + t.y^2 (node near this door?)
        a.raw(b'\xD9\x06')                                # fld node.x
        a.raw(b'\xD8\x27')                                # fsub door.x   -> t1
        a.raw(b'\xD9\x46\x04')                            # fld node.y
        a.raw(b'\xD8\x67\x04')                            # fsub door.y   -> t2, t1
        a.raw(b'\xD9\xC1')                                # fld st1       -> t1, t2, t1
        a.raw(b'\xD8\xC8')                                # fmul st0,st0  -> t1^2, t2, t1
        a.raw(b'\xD9\xC1')                                # fld st1       -> t2, t1^2, t2, t1
        a.raw(b'\xD8\xC8')                                # fmul st0,st0  -> t2^2, ...
        a.raw(b'\xDE\xC1')                                # faddp         -> d2, t2, t1
        a.raw(b'\xD9\x05' + le32(door_wedge_radius_sq_va))  # fld R -> R, d2, t2, t1
        a.raw(b'\xDF\xF1')                                # fcomip R:d2; pop R
        a.jb('s542360_wp_ag_pop3')                        # R < d2 -> node not at door
        a.raw(b'\xDD\xD8')                                # fstp (drop d2) -> t2, t1
        # dot = t1*(bot.x - door.x) + t2*(bot.y - door.y)
        a.raw(b'\xD9\x05' + le32(bot_pos_va))             # fld bot.x -> s1, t2, t1
        a.raw(b'\xD8\x27')                                # fsub door.x
        a.raw(b'\xDE\xCA')                                # fmulp st2,st0 -> t2, s1*t1
        a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))         # fld bot.y -> s2, t2, s1t1
        a.raw(b'\xD8\x67\x04')                            # fsub door.y
        a.raw(b'\xDE\xC9')                                # fmulp st1,st0 -> s2*t2, s1t1
        a.raw(b'\xDE\xC1')                                # faddp         -> dot
        a.raw(b'\xD9\xE4')                                # ftst (dot vs +0.0)
        a.raw(b'\xDF\xE0')                                # fnstsw ax — EAX is dead here
                                                          # (node ptr lives in ESI;
                                                          # AGENTS constraint #6)
        a.raw(b'\x9E')                                    # sahf (C0->CF)
        a.raw(b'\xDD\xD8')                                # fstp (pop dot; EFLAGS kept)
        a.jb('s542360_wp_ag_refuse')                      # dot < 0 -> across the door
        a.jmp('s542360_wp_ag_next')
        a.label('s542360_wp_ag_pop3')
        a.raw(b'\xDD\xD8')                                # drop d2
        a.raw(b'\xDD\xD8')                                # drop t2
        a.raw(b'\xDD\xD8')                                # drop t1
        a.label('s542360_wp_ag_next')
        a.raw(b'\x42')                                    # ++door idx
        a.jmp('s542360_wp_ag_loop')
        a.label('s542360_wp_ag_refuse')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))        # ecx = slot (expected below)
        a.jmp('s542360_wp_no_progress_popped')
        a.label('s542360_wp_ag_ok')
    # Reached the node: advance to a CONNECTED neighbour (random; prefers !=
    # prev). When not latched (prev == -1) pass cur as prev so the advance
    # latches and any neighbour is acceptable.
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x0C\x8D' + le32(current_wp_va))          # ecx = cur
    a.raw(b'\x51')                                        # push cur
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(prev_wp_va))             # edx = prev
    a.raw(b'\x59')                                        # pop ecx (= cur)
    a.raw(b'\x83\xFA\xFF')                                # cmp edx, -1
    a.jnz('s542360_wp_do_adv')
    a.raw(b'\x89\xCA')                                    # edx = cur (latch)
    a.label('s542360_wp_do_adv')
    # CTF goal-biased routing: in a CTF match, step one hop along the shortest
    # path toward the goal flag base (ctf_next_hop) instead of a random neighbour.
    # -1 (routing inactive / non-CTF / no progress here) => fall back to the
    # random wp_advance, byte-identical to the non-CTF behaviour. ECX=cur,
    # EDX=prev are live here; both are saved across ctf_next_hop so wp_advance
    # gets them back on the fallback path. If the progress watchdog marked this
    # edge as blocked, force the fallback and pass the blocked next-hop as the
    # "previous" node to wp_advance so random fallback also avoids it.
    if cfg.CTF_FLAG_ROUTING_ENABLED:
        a.raw(b'\x51')                                   # push cur (ecx)
        a.raw(b'\x52')                                   # push prev (edx)
        if drop_move or chase_move:
            if drop_move:
                # Dropped-flag route override: while this bot's pursuit latch
                # is set and its routing is NOT suspended (the suspension roam
                # exists to unstick deterministic routing — don't override
                # it), descend the per-drop BFS row instead of the goal
                # field. Falls back to the chase/SK/ctf dispatch below when
                # the row can't apply (helper returns -1: drop gone, row
                # stale/unbuilt, node unreachable).
                a.raw(b'\xA1' + le32(bot_slot_tmp_va))   # eax = slot
                a.raw(b'\x83\x3C\x85' + le32(bot_drop_target_va) + b'\x00')
                a.jz('s542360_wp_use_cnh')
                a.raw(b'\x83\x3C\x85' + le32(route_suspend_va) + b'\x00')
                a.jnz('s542360_wp_use_cnh')
                a.call_lbl('drop_next_hop')              # eax = drop hop or -1 (in: ecx=cur)
                a.raw(b'\x83\xF8\xFF')
                a.jnz('s542360_wp_hop_done')             # got a drop hop
                a.raw(b'\x8B\x4C\x24\x04')               # ecx = cur (helper clobbered it)
                a.label('s542360_wp_use_cnh')
            if chase_move:
                # Enemy-carrier chase route override (below the drop latch —
                # the chase block already unlatches itself when a drop
                # pursuit engages): descend the per-flag carrier row.
                # Respects the routing suspension like the drop descent.
                a.raw(b'\xA1' + le32(bot_slot_tmp_va))   # eax = slot
                a.raw(b'\x83\x3C\x85' + le32(bot_chase_flag_va) + b'\x00')
                a.jz('s542360_wp_no_chs_hop')
                a.raw(b'\x83\x3C\x85' + le32(route_suspend_va) + b'\x00')
                a.jnz('s542360_wp_no_chs_hop')
                a.call_lbl('chase_next_hop')             # eax = chase hop or -1 (in: ecx=cur)
                a.raw(b'\x83\xF8\xFF')
                a.jnz('s542360_wp_hop_done')             # got a chase hop
                a.raw(b'\x8B\x4C\x24\x04')               # ecx = cur (helper clobbered it)
                a.label('s542360_wp_no_chs_hop')
            if sk_move:
                # SK matches: descend the mineral/own-bin field instead of
                # the (inert-in-SK) CTF goal field. Also fires OUTSIDE SK
                # whenever this bot has a goody pursuit latched (filler-item
                # divert in DM/CTF) — sk_next_hop's kind row-select serves
                # it. -1 falls through to ctf_next_hop, which also returns
                # -1 in SK, and then to the random wp_advance — exactly the
                # mineral-zone roam.
                a.raw(b'\x83\x3D' + le32(sk_active_mv_va) + b'\x00')
                a.jnz('s542360_wp_sk_hop')
                if goody_move:
                    a.raw(b'\x83\x3D' + le32(item_active_mv_va) + b'\x00')
                    a.jz('s542360_wp_no_sk_hop')
                    a.raw(b'\xA1' + le32(bot_slot_tmp_va))    # eax = slot
                    a.raw(b'\x83\x3C\x85' + le32(bot_pile_target_va) + b'\x00')
                    a.jz('s542360_wp_no_sk_hop')              # no pursuit -> skip
                else:
                    a.jmp('s542360_wp_no_sk_hop')
                a.label('s542360_wp_sk_hop')
                a.call_lbl('sk_next_hop')                # eax = hop or -1 (in: ecx=cur)
                a.raw(b'\x83\xF8\xFF')
                a.jnz('s542360_wp_hop_done')             # got a hop
                a.raw(b'\x8B\x4C\x24\x04')               # ecx = cur (helper clobbered it)
                a.label('s542360_wp_no_sk_hop')
            a.call_lbl('ctf_next_hop')                   # eax = goal next-hop or -1 (in: ecx=cur)
            a.label('s542360_wp_hop_done')
        else:
            a.call_lbl('ctf_next_hop')                   # eax = goal next-hop or -1 (in: ecx=cur)
        a.raw(b'\x5A')                                   # pop edx (prev)
        a.raw(b'\x59')                                   # pop ecx (cur)
        a.raw(b'\x83\xF8\xFF')                           # cmp eax, -1
        a.jz('s542360_wp_route_fallback')                # no route -> random neighbour
        if portal_move:
            # Routed PORTAL hop: ctf_next_hop parked the winning pad idx+1 in
            # route_portal_hop (and returned cur). Latch the pad approach —
            # the have_cur block takes over from the next think — and keep
            # current_wp on the pad's node so the graph latch stays sane.
            a.raw(b'\x83\x3D' + le32(route_portal_hop_va) + b'\x00')
            a.jz('s542360_wp_no_portal_hop')
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            a.raw(b'\x8B\x35' + le32(route_portal_hop_va))  # esi = pad idx+1
            a.raw(b'\x89\x34\x9D' + le32(bot_portal_target_va))  # latch
            a.raw(b'\xC7\x05' + le32(route_portal_hop_va) + le32(0))
            a.raw(b'\xC7\x04\x9D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # fresh watchdog
            a.raw(b'\xC7\x04\x9D' + le32(wp_try_va) + le32(0))
            if layout.has_field('bot_pad_try'):
                a.raw(b'\xC7\x04\x9D' + le32(layout.va('bot_pad_try')) + le32(0))
            a.jmp('s542360_wp_steer')
            a.label('s542360_wp_no_portal_hop')
        a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))       # ebx = slot
        a.raw(b'\x8B\x1C\x9D' + le32(failed_edge_va))    # ebx = failed_edge_marker[slot]
        a.raw(b'\x85\xDB')                               # test ebx, ebx
        a.jz('s542360_wp_have_next')
        # candidate marker = unordered(cur, route_next)
        a.raw(b'\x89\xCE')                               # esi = cur
        a.raw(b'\x89\xC7')                               # edi = route_next
        a.raw(b'\x39\xFE')                               # cmp esi, edi
        a.jbe('s542360_wp_route_edge_ordered')
        a.raw(b'\x87\xFE')                               # xchg esi, edi
        a.label('s542360_wp_route_edge_ordered')
        a.raw(b'\x46')                                   # inc esi (min+1)
        a.raw(b'\x47')                                   # inc edi (max+1)
        a.raw(b'\xC1\xE7\x10')                           # shl edi, 16
        a.raw(b'\x09\xFE')                               # or esi, edi
        a.raw(b'\x39\xDE')                               # cmp esi, ebx
        a.jz('s542360_wp_bad_edge_fallback')
        # Clean routed hop while a marker exists: DO NOT reset the ping-pong
        # counter. It used to start over here ("consecutive" forced-fallback
        # semantics), which a two-node shuttle defeats BY CONSTRUCTION: live
        # CE on a Hydro dropped-flag pursuit caught the descent alternating
        # forced-off-the-marker (36: marker (34,36) -> random -> 37) with a
        # clean descent hop (37 -> 36) every cycle, so hits ping-ponged
        # 0<->1 forever and the marker never expired — the bot shuffled
        # between the two nodes until a human took the flag. The budget now
        # counts TOTAL forced events per marker lifetime; every existing
        # reset (marker re-set, blind retry, reacquire, suspension expiry,
        # door fast-clear, respawn, match change) still applies.
        a.jmp('s542360_wp_have_next')
        a.label('s542360_wp_bad_edge_fallback')
        # Routing insists on the marked edge (it IS the shortest path). Count
        # the forced fallbacks: on a graph like door nodes with one alternate
        # neighbour, routing bounces the bot right back here every hop — an
        # arrival-level ping-pong that never trips the wedge timeout (live CE:
        # cur flipped 17<->18 with wp_try pinned at 0). After
        # WP_ROUTE_BLOCK_RETRY_HITS forced fallbacks, clear the marker so the
        # next arrival RETRIES the edge: if the way is open now (doors open
        # when their area is awake) the bot just walks through; if it is still
        # blocked the wedge timeout re-marks it and arms the roam suspension.
        a.raw(b'\x8B\x35' + le32(bot_slot_tmp_va))       # esi = slot
        a.raw(b'\xFF\x04\xB5' + le32(route_block_hits_va))  # ++hits[slot]
        a.raw(b'\x83\x3C\xB5' + le32(route_block_hits_va)
              + bytes([cfg.WP_ROUTE_BLOCK_RETRY_HITS]))  # hits >= retry threshold?
        a.jb('s542360_wp_bad_edge_go')
        a.raw(b'\xC7\x04\xB5' + le32(failed_edge_va) + le32(0))       # retry the edge
        a.raw(b'\xC7\x04\xB5' + le32(route_block_hits_va) + le32(0))
        if door_gate:
            a.raw(b'\xC7\x04\xB5' + le32(route_block_door_va) + b'\xFF\xFF\xFF\xFF')
        a.label('s542360_wp_bad_edge_go')
        a.raw(b'\x89\xC2')                               # edx = blocked route_next
        a.call_lbl('wp_advance')                         # fallback excluding the blocked edge
        a.jmp('s542360_wp_have_next')
        a.label('s542360_wp_route_fallback')
        if portal_move:
            # Roam wander-entry (DM matches, goal-less CTF bots): if the
            # just-reached node hosts an active pad, roll
            # portal_wander_chance and occasionally step INTO the teleporter
            # instead of picking a random neighbour. cur/prev survive the
            # helper via the stack (it calls the engine RNG). SKIPPED while
            # this bot's routing is SUSPENDED (a suspension roam is a local
            # unstick — live snapshots caught a suspended carrier bouncing
            # arena-to-arena on this roll) and during the post-teleport
            # cooldown (each pad's exit node IS the return pad's node, so
            # the very next arrival would re-roll the coin — the observed
            # teleport ping-pong).
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            if routing:
                a.raw(b'\x83\x3C\x9D' + le32(route_suspend_va) + b'\x00')
                a.jnz('s542360_wp_no_wander')            # suspended -> local roam only
            if cfg.CTF_DEFENDER_ENABLED and layout.has_field('bot_role'):
                # A DEFENDER's near-base roam must never ride a teleporter:
                # the pad exit is usually far outside its patrol radius, so
                # the roll would just bounce it out and back (Hydro pads
                # cross arenas). Its tether re-routes it home anyway; skip
                # the coin entirely.
                a.raw(b'\xF6\x04\x9D' + le32(layout.va('bot_role')) + b'\x01')
                a.jnz('s542360_wp_no_wander')            # defender (bit0) -> no pad roll
            if (cfg.CTF_DEFENDER_ENABLED and cfg.CTF_CARRIER_STANDOFF_ENABLED
                    and layout.has_field('defend_radius')
                    and layout.has_field('flag_routing_active')
                    and layout.has_field('route_carry')):
                # STANDOFF/carrier pad skip: a CARRIER on this fallback path
                # is either tether-roaming at its base (standoff) or
                # suspension-adjacent — a pad ride dumps it across the map
                # with the flag. route_carry is fresh for THIS bot only when
                # routing is armed (ctf_next_hop ran ctf_pick_goal this
                # arrival), so gate on flag_routing_active first — a stale
                # value must never eat the DM/SK pad roll.
                a.raw(b'\x83\x3D' + le32(layout.va('flag_routing_active')) + b'\x00')
                a.jz('s542360_wp_wander_nc')
                a.raw(b'\x83\x3D' + le32(layout.va('route_carry')) + b'\x00')
                a.jnz('s542360_wp_no_wander')            # carrier -> no pad roll
                a.label('s542360_wp_wander_nc')
            if layout.has_field('bot_portal_cd'):
                a.raw(b'\x83\x3C\x9D' + le32(layout.va('bot_portal_cd')) + b'\x00')
                a.jnz('s542360_wp_no_wander')            # just teleported -> no re-entry
            a.raw(b'\x51')                               # push cur (ecx)
            a.raw(b'\x52')                               # push prev (edx)
            a.call_lbl('portal_wander_check')            # eax = pad idx+1 or 0 (in: ecx=cur)
            a.raw(b'\x5A')                               # pop edx (prev)
            a.raw(b'\x59')                               # pop ecx (cur)
            a.raw(b'\x85\xC0'); a.jz('s542360_wp_no_wander')
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            a.raw(b'\x89\x04\x9D' + le32(bot_portal_target_va))  # latch = pad idx+1
            a.raw(b'\xC7\x04\x9D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
            a.raw(b'\xC7\x04\x9D' + le32(wp_try_va) + le32(0))
            if layout.has_field('bot_pad_try'):
                a.raw(b'\xC7\x04\x9D' + le32(layout.va('bot_pad_try')) + le32(0))
            a.jmp('s542360_wp_steer')
            a.label('s542360_wp_no_wander')
        if switch_wander:
            # Roam switch-bump roll: same arrival, after the pad roll (a pad
            # entry outranks a bump — it rewires the whole roam). Skipped
            # while the per-bot cooldown runs or a bump is already latched;
            # deliberately NOT skipped during routing suspension — unlike a
            # teleport, a bump is local and can open the exact door the bot
            # is wedged at (the suspension roam brought it here).
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            a.raw(b'\x83\x3C\x9D' + le32(bot_switch_cd_va) + b'\x00')
            a.jnz('s542360_wp_no_sww')                   # cooling down -> no roll
            a.raw(b'\x83\x3C\x9D' + le32(bot_switch_target_va) + b'\x00')
            a.jnz('s542360_wp_no_sww')                   # already latched
            a.raw(b'\x51')                               # push cur (ecx)
            a.raw(b'\x52')                               # push prev (edx)
            a.call_lbl('switch_wander_check')            # eax = switch idx+1 or 0 (in: ecx=cur)
            a.raw(b'\x5A')                               # pop edx (prev)
            a.raw(b'\x59')                               # pop ecx (cur)
            a.raw(b'\x85\xC0'); a.jz('s542360_wp_no_sww')
            a.raw(b'\x8B\x1D' + le32(bot_slot_tmp_va))   # ebx = slot
            a.raw(b'\x89\x04\x9D' + le32(bot_switch_target_va))  # latch = idx+1
            a.raw(b'\xA1' + le32(sww_census_va))         # eax = roll-time census
            a.raw(b'\x89\x04\x9D' + le32(bot_switch_snap_va))
            a.raw(b'\xC7\x04\x9D' + le32(bot_switch_try_va) + le32(0))
            a.raw(b'\xC7\x04\x9D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))
            a.raw(b'\xC7\x04\x9D' + le32(wp_try_va) + le32(0))
            a.jmp('s542360_wp_steer')
            a.label('s542360_wp_no_sww')
        a.call_lbl('wp_advance')                         # fallback: random/non-prev neighbour
        a.label('s542360_wp_have_next')
    else:
        a.call_lbl('wp_advance')                          # eax = next idx or -1
    a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(current_wp_va))          # edx = old cur
    a.raw(b'\x89\x14\x8D' + le32(prev_wp_va))             # prev_wp[slot] = old cur (LATCH)
    a.raw(b'\x83\xF8\xFF')                                # cmp eax, -1
    a.jz('s542360_wp_steer')                              # isolated node -> keep cur
    a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = next
    a.raw(b'\xC7\x04\x8D' + le32(wp_best_dsq_va) + le32(0x7F7FFFFF))  # best_dsq = FLT_MAX
    a.raw(b'\xC7\x04\x8D' + le32(wp_try_va) + le32(0))               # wp_try = 0
    # Keep failed_edge_marker until respawn/reacquire or another edge failure;
    # otherwise CTF routing immediately reselects the same bad direct edge.
    # fall through to steer toward the (new) current node

