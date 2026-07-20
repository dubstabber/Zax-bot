"""Closed-door commitment recovery: back a bot off an edge bound to a
now-closed door (door-side crossed test) so the arrival re-plan runs
door-aware from the reachable node."""

from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    bot_pos_va = c.bot_pos_va
    bot_slot_tmp_va = c.bot_slot_tmp_va
    current_wp_va = c.current_wp_va
    prev_wp_va = c.prev_wp_va
    overlay_vertices_va = c.overlay_vertices_va
    dy_accum_va = c.dy_accum_va
    routing_active_va = c.routing_active_va
    door_blocked_va = c.door_blocked_va
    door_count_va = c.door_count_va
    door_reroute = c.door_reroute
    edge_door_va = c.edge_door_va
    door_table_va = c.door_table_va
    overlay_edges_va = c.overlay_edges_va
    overlay_edge_count_va = c.overlay_edge_count_va

    if door_reroute:
        # --- Closed-door commitment recovery -------------------------------
        # If the (prev -> cur) edge we are latched onto is bound to a currently
        # CLOSED door and we are still on the PREV side (nearer prev than cur, so
        # we have not crossed), the last leg is impassable and arrival — the only
        # thing that re-runs the door-aware ctf_next_hop — will never happen. Back
        # the target up to prev and jump into the arrival/advance path so
        # ctf_next_hop re-plans door-aware from the reachable node. Fires only in
        # exactly this stuck state (door open, or already crossed => no-op).
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')  # routing active?
        a.jz('s542360_cdr_done')
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
        a.raw(b'\x8B\x3C\x8D' + le32(prev_wp_va))             # edi = prev_wp[slot]
        a.raw(b'\x83\xFF\xFF')                                # cmp edi, -1
        a.jz('s542360_cdr_done')                              # not latched
        a.raw(b'\x8B\x04\x8D' + le32(current_wp_va))          # eax = current_wp[slot]
        a.raw(b'\x39\xC7')                                    # cmp edi, eax (prev == cur?)
        a.jz('s542360_cdr_done')
        a.raw(b'\xA3' + le32(dy_accum_va))                    # spill cur -> dy_accum (cand_tmp)
        a.raw(b'\x31\xF6')                                    # esi = 0 (edge idx)
        a.label('s542360_cdr_scan')
        a.raw(b'\x3B\x35' + le32(overlay_edge_count_va))      # cmp esi, edge_count
        a.jae('s542360_cdr_done')                             # (prev,cur) edge not found
        a.raw(b'\x8B\x04\xB5' + le32(overlay_edges_va))       # eax = edges[esi]
        a.raw(b'\x0F\xB7\xD8')                                # movzx ebx, ax (i)
        a.raw(b'\xC1\xE8\x10')                                # shr eax, 16   (j)
        a.raw(b'\x39\xFB')                                    # cmp ebx, edi (i == prev?)
        a.jnz('s542360_cdr_swap')
        a.raw(b'\x3B\x05' + le32(dy_accum_va))                # cmp eax, cur (j == cur?)
        a.jz('s542360_cdr_found')
        a.jmp('s542360_cdr_next')
        a.label('s542360_cdr_swap')
        a.raw(b'\x3B\x1D' + le32(dy_accum_va))                # cmp ebx, cur (i == cur?)
        a.jnz('s542360_cdr_next')
        a.raw(b'\x39\xF8')                                    # cmp eax, edi (j == prev?)
        a.jz('s542360_cdr_found')
        a.label('s542360_cdr_next')
        a.raw(b'\x46')                                        # inc esi
        a.jmp('s542360_cdr_scan')
        a.label('s542360_cdr_found')
        a.raw(b'\x8B\x14\xB5' + le32(edge_door_va))           # edx = edge_door[esi]
        a.raw(b'\x83\xFA\xFF')                                # cmp edx, -1
        a.jz('s542360_cdr_done')                              # no door on this edge
        a.raw(b'\x3B\x15' + le32(door_count_va))              # cmp edx, door_count
        a.jae('s542360_cdr_done')                             # stale idx
        a.raw(b'\x83\x3C\x95' + le32(door_blocked_va) + b'\x00')  # door blocked?
        a.jz('s542360_cdr_done')                              # open -> normal handling
        # Closed door on the committed edge. Has the bot CROSSED the door?
        # Test the DOOR side, not node proximity: the door is rarely at the
        # edge midpoint (Battle on the Ice: 30 px from node 47, 170 px from
        # node 48), so the old "nearer prev than cur" test mislabelled a bot
        # standing just past the doorway as not-crossed and walked it back
        # INTO the closed door (live 2026-07-20 dpursuit/rstate snapshots:
        # the backwards-forwards shuttle at the self-closing team door).
        # crossed = dot(bot - door, cur - prev) > 0 -> no-op (the bot is on
        # the cur side; arrival at cur re-plans); <= 0 (incl. degenerate
        # zero/NaN) -> back up to prev. edi = prev idx, edx = door idx (in
        # range, checked above), cur idx in dy_accum.
        a.raw(b'\x8D\x04\xD5' + le32(door_table_va))          # lea eax, [edx*8 + door_table]
        a.raw(b'\x8D\x0C\xFD' + le32(overlay_vertices_va))    # lea ecx, [edi*8 + verts] (prev)
        a.raw(b'\x8B\x1D' + le32(dy_accum_va))                # ebx = cur idx
        a.raw(b'\x8D\x1C\xDD' + le32(overlay_vertices_va))    # lea ebx, [ebx*8 + verts] (cur)
        a.raw(b'\xD9\x05' + le32(bot_pos_va))                 # fld bot.x
        a.raw(b'\xD8\x20')                                    # fsub door.x ([eax])
        a.raw(b'\xD9\x03')                                    # fld cur.x ([ebx])
        a.raw(b'\xD8\x21')                                    # fsub prev.x ([ecx])
        a.raw(b'\xDE\xC9')                                    # fmulp -> (b.x-d.x)*(c.x-p.x)
        a.raw(b'\xD9\x05' + le32(bot_pos_va + 4))             # fld bot.y
        a.raw(b'\xD8\x60\x04')                                # fsub door.y ([eax+4])
        a.raw(b'\xD9\x43\x04')                                # fld cur.y ([ebx+4])
        a.raw(b'\xD8\x61\x04')                                # fsub prev.y ([ecx+4])
        a.raw(b'\xDE\xC9')                                    # fmulp
        a.raw(b'\xDE\xC1')                                    # faddp -> dot product (st0)
        a.raw(b'\xD9\xE4')                                    # ftst (st0 vs +0.0)
        a.raw(b'\xDF\xE0')                                    # fnstsw ax — eax is dead here
                                                              # (door ptr fully consumed;
                                                              # AGENTS constraint #6)
        a.raw(b'\x9E')                                        # sahf (ZF=C3, CF=C0)
        a.raw(b'\xDD\xD8')                                    # fstp st0 (pop; EFLAGS kept)
        a.ja('s542360_cdr_done')                              # dot > 0: crossed -> no-op
        # Still on the prev side of the door: re-plan door-aware from prev.
        a.raw(b'\x8B\x0D' + le32(bot_slot_tmp_va))            # ecx = slot
        a.raw(b'\x8B\x04\x8D' + le32(prev_wp_va))             # eax = prev_wp[slot]
        a.raw(b'\x89\x04\x8D' + le32(current_wp_va))          # current_wp[slot] = prev
        a.jmp('s542360_wp_arrived')
        a.label('s542360_cdr_done')
    # --- CTF final approach -------------------------------------------------
    # Once the bot's current node IS the nearest node to its goal flag, the
    # graph can take it no closer — so steer straight at the actual flag base
    # position to physically touch it (grab the enemy flag, or deliver to own
    # base to capture). Without this the bot "arrives" at the node, ctf_next_hop
    # finds no closer neighbour, the random wp_advance fallback bounces it to a
    # neighbour and routing snaps it back -> it circles the node forever and
    # never reaches the flag. ctf_pick_goal recomputes the goal every frame, so
    # the instant the bot grabs the flag the goal flips to home and this branch
    # stops firing (cur != home goal node) -> normal routing resumes.
