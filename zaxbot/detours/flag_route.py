"""CTF flag routing — ``build_flag_routes`` (per-match BFS) + ``ctf_next_hop``
(per-arrival goal-biased next-hop over the authored waypoint graph).

The follower (``detours/bot_movement.py``) normally advances to a RANDOM
connected neighbour on node arrival (``wp_advance``). In a CTF match these two
subroutines replace that random pick with a true shortest-path step toward a
flag base:

* NOT carrying the flag  -> head to the ENEMY base.
* carrying the enemy flag -> head to OWN base to capture.

**Model.** Goals are the static flag BASE anchors (``flag_table`` + ``flag_team``,
loaded per match by ``load_flags``). Routing is a precomputed BFS field, NOT a
per-frame search:

* ``build_flag_routes`` (once per match, from ``detour_df90`` when the match is
  CTF with a graph + flags): for each routed base i, finds the nearest graph
  node (``wp_find_nearest``) and runs a breadth-first search over the UNDIRECTED
  edge list, filling ``flag_dist[i][node]`` with the hop distance from that base
  node to every node (``0xFFFFFFFF`` = unreachable / no graph). O(V*E) once.
* ``ctf_next_hop`` (each node arrival, from ``s542360_wp_arrived``): picks the
  bot's goal base from its team (``bot_team[slot]``) and carry state
  (``is_carrying``), then returns the neighbour of the current node whose
  ``flag_dist[goal]`` is strictly smaller than the current node's — i.e. one hop
  along a real shortest path, guaranteeing progress. Returns ``-1`` (caller
  falls back to the random ``wp_advance``) whenever routing can't apply: routing
  inactive, no goal base for this team, the current node is unreachable from the
  goal, or the bot already sits on the goal node.

Live flag-base presence (``flag_present[]``) is EVENT-driven: the
``detours/flag_events.py`` detours mirror the map script's base-checker
activation (deactivated on steal, reactivated on return/capture), which is the
vanilla "own flag is home" state. When an attacker sees the enemy flag missing
from its base,
the bot rolls one temporary policy for that missing-goal episode: either search
(``route_goal_flag = -1``, so node arrivals fall back to random roaming), or
wait near the enemy base (keep the goal so BFS moves it toward that home
anchor). A carrier whose OWN flag is missing must not be driven into the empty
home base, because capture is illegal until that flag returns; carrier+missing
home always uses search mode. The policy is cleared when the flag becomes
present again.

``is_carrying`` is the engine's own per-character inventory-group test
(live-verified): ``inv = sub_4267E0(char)``; ``slot = sub_425290(inv, FLAG_GID)``
where ``FLAG_GID = [MULTIPLAYER_FLAG_GID_VA]`` (==8); carrying iff ``slot != -1``.
Every deref is NULL-guarded (``inv == 0`` / ``FLAG_GID == 0`` => not carrying).

All routing data is GLOBAL scratch (not per-bot). ``flag_routing_active`` (set by
``detour_df90``) is the master runtime gate.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    needed = (
        'flag_routing_active', 'route_cur', 'route_carry', 'route_goal_flag',
        'flag_route_node', 'flag_dist', 'bfs_queue', 'bfs_head', 'bfs_tail',
        'bfs_u', 'bfs_du', 'bfs_disti', 'bfr_i', 'flag_table', 'flag_team',
        'flag_count', 'flag_present', 'route_missing_policy',
        'route_missing_goal', 'overlay_vertices', 'overlay_vertex_count',
        'overlay_edges', 'overlay_edge_count', 'wp_scratch', 'bot_slot_tmp',
        'bot_char_tmp', 'bot_team',
    )
    if not all(layout.has_field(f) for f in needed):
        # Layout built without routing fields — inert stubs so call_lbl resolves.
        a.label('build_flag_routes'); a.raw(b'\xC3')
        a.label('ctf_pick_goal'); a.raw(b'\xC3')
        a.label('ctf_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')  # mov eax,-1; ret
        return

    routing_active_va = layout.va('flag_routing_active')
    route_cur_va      = layout.va('route_cur')
    route_carry_va    = layout.va('route_carry')
    route_goal_va     = layout.va('route_goal_flag')
    route_node_va     = layout.va('flag_route_node')
    flag_dist_va      = layout.va('flag_dist')
    bfs_queue_va      = layout.va('bfs_queue')
    bfs_head_va       = layout.va('bfs_head')
    bfs_tail_va       = layout.va('bfs_tail')
    bfs_u_va          = layout.va('bfs_u')
    bfs_du_va         = layout.va('bfs_du')
    bfs_disti_va      = layout.va('bfs_disti')
    bfr_i_va          = layout.va('bfr_i')
    flag_table_va     = layout.va('flag_table')
    flag_team_va      = layout.va('flag_team')
    flag_count_va     = layout.va('flag_count')
    flag_present_va   = layout.va('flag_present')
    missing_policy_va = layout.va('route_missing_policy')
    missing_goal_va   = layout.va('route_missing_goal')
    route_suspend_va  = layout.va('bot_route_suspend')
    verts_va          = layout.va('overlay_vertices')
    vcount_va         = layout.va('overlay_vertex_count')
    edges_va          = layout.va('overlay_edges')
    ecount_va         = layout.va('overlay_edge_count')
    wp_scratch_va     = layout.va('wp_scratch')
    bot_slot_va       = layout.va('bot_slot_tmp')
    bot_char_va       = layout.va('bot_char_tmp')
    bot_team_va       = layout.va('bot_team')

    VMAX  = cfg.OVERLAY_VERTEX_MAX
    RMAX  = cfg.FLAG_ROUTE_MAX
    ROW   = VMAX * 4                       # flag_dist row stride (bytes) per base

    # =====================================================================
    # build_flag_routes: per-match BFS distance field from each flag base.
    # pushad/popad, no args. Safe to call even with no graph (dist stays INF).
    # =====================================================================
    a.label('build_flag_routes')
    a.raw(b'\x60')                                              # pushad
    # flag_dist[*] = 0xFFFFFFFF
    a.raw(b'\xBF' + le32(flag_dist_va))                        # edi = flag_dist
    a.raw(b'\xB9' + le32(RMAX * VMAX))                         # ecx = RMAX*VMAX
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # eax = -1
    a.raw(b'\xFC')                                             # cld
    a.raw(b'\xF3\xAB')                                         # rep stosd
    # flag_route_node[*] = -1
    a.raw(b'\xBF' + le32(route_node_va))                      # edi = flag_route_node
    a.raw(b'\xB9' + le32(RMAX))                                # ecx = RMAX
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # eax = -1
    a.raw(b'\xF3\xAB')                                         # rep stosd
    # No graph? leave INF and bail.
    a.raw(b'\xA1' + le32(vcount_va))                           # eax = vertex_count
    a.raw(b'\x85\xC0'); a.jz('bfr_done')
    a.raw(b'\xC7\x05' + le32(bfr_i_va) + le32(0))             # bfr_i = 0

    a.label('bfr_loop')
    # nbase = min(flag_count, RMAX); stop when bfr_i >= nbase.
    a.raw(b'\x8B\x0D' + le32(flag_count_va))                  # ecx = flag_count
    a.raw(b'\x83\xF9' + bytes([RMAX]))                        # cmp ecx, RMAX
    a.jbe('bfr_nb_ok')
    a.raw(b'\xB9' + le32(RMAX))                                # ecx = RMAX
    a.label('bfr_nb_ok')
    a.raw(b'\xA1' + le32(bfr_i_va))                           # eax = i
    a.raw(b'\x39\xC8')                                         # cmp eax, ecx (i - nbase)
    a.jae('bfr_done')
    # wp_scratch = flag_table[i]
    a.raw(b'\x8B\x0C\xC5' + le32(flag_table_va))             # ecx = [flag_table + eax*8] (x)
    a.raw(b'\x89\x0D' + le32(wp_scratch_va))                  # wp_scratch.x = ecx
    a.raw(b'\x8B\x0C\xC5' + le32(flag_table_va + 4))         # ecx = [flag_table + eax*8 + 4] (y)
    a.raw(b'\x89\x0D' + le32(wp_scratch_va + 4))             # wp_scratch.y = ecx
    a.call_lbl('wp_find_nearest')                             # ebx = nearest idx or -1
    a.raw(b'\x83\xFB\xFF')                                     # cmp ebx, -1
    a.jz('bfr_next')                                          # no node -> skip base
    a.raw(b'\xA1' + le32(bfr_i_va))                           # eax = i
    a.raw(b'\x89\x1C\x85' + le32(route_node_va))             # flag_route_node[i] = ebx
    # disti = flag_dist + i*ROW
    a.raw(b'\x69\xC0' + le32(ROW))                            # imul eax, eax, ROW
    a.raw(b'\x05' + le32(flag_dist_va))                       # add eax, flag_dist
    a.raw(b'\xA3' + le32(bfs_disti_va))                       # bfs_disti = eax
    # BFS init: disti[nearest]=0; queue[0]=nearest; head=0; tail=1
    a.raw(b'\x8B\x0D' + le32(bfs_disti_va))                  # ecx = disti
    a.raw(b'\xC7\x04\x99\x00\x00\x00\x00')                   # mov [ecx + ebx*4], 0
    a.raw(b'\x89\x1D' + le32(bfs_queue_va))                  # queue[0] = ebx
    a.raw(b'\xC7\x05' + le32(bfs_head_va) + le32(0))         # head = 0
    a.raw(b'\xC7\x05' + le32(bfs_tail_va) + le32(1))         # tail = 1

    a.label('bfr_bfs_loop')
    a.raw(b'\xA1' + le32(bfs_head_va))                        # eax = head
    a.raw(b'\x3B\x05' + le32(bfs_tail_va))                   # cmp eax, tail
    a.jae('bfr_bfs_done')
    a.raw(b'\x8B\x0C\x85' + le32(bfs_queue_va))             # ecx = queue[head]
    a.raw(b'\x89\x0D' + le32(bfs_u_va))                      # bfs_u = u
    a.raw(b'\xFF\x05' + le32(bfs_head_va))                  # head++
    a.raw(b'\x8B\x15' + le32(bfs_disti_va))                 # edx = disti
    a.raw(b'\x8B\x04\x8A')                                   # eax = [edx + ecx*4] (disti[u] = du)
    a.raw(b'\x89\x05' + le32(bfs_du_va))                    # bfs_du = du
    a.raw(b'\x31\xF6')                                       # esi = 0 (edge idx)

    a.label('bfr_edge_loop')
    a.raw(b'\x3B\x35' + le32(ecount_va))                    # cmp esi, edge_count
    a.jae('bfr_bfs_loop')                                   # edges done -> next BFS node
    a.raw(b'\x8B\x04\xB5' + le32(edges_va))                # eax = edges[esi]
    a.raw(b'\x0F\xB7\xD8')                                  # movzx ebx, ax (i)
    a.raw(b'\xC1\xE8\x10')                                  # shr eax, 16   (j)
    a.raw(b'\x8B\x0D' + le32(bfs_u_va))                    # ecx = u
    a.raw(b'\x39\xCB')                                      # cmp ebx, ecx (i == u?)
    a.jz('bfr_edge_v_j')                                    # i==u -> v = j (eax)
    a.raw(b'\x39\xC8')                                      # cmp eax, ecx (j == u?)
    a.jz('bfr_edge_v_i')                                    # j==u -> v = i (ebx)
    a.jmp('bfr_edge_next')
    a.label('bfr_edge_v_i')
    a.raw(b'\x89\xD8')                                      # eax = ebx (v = i)
    a.label('bfr_edge_v_j')                                # v in eax
    a.raw(b'\x3B\x05' + le32(vcount_va))                   # cmp eax, vertex_count
    a.jae('bfr_edge_next')                                 # out of range
    a.raw(b'\x8B\x15' + le32(bfs_disti_va))               # edx = disti
    a.raw(b'\x8B\x0C\x82')                                 # ecx = [edx + eax*4] (disti[v])
    a.raw(b'\x83\xF9\xFF')                                 # cmp ecx, -1 (visited?)
    a.jnz('bfr_edge_next')
    a.raw(b'\x8B\x0D' + le32(bfs_du_va))                  # ecx = du
    a.raw(b'\x41')                                         # inc ecx (du+1)
    a.raw(b'\x89\x0C\x82')                                # [edx + eax*4] = ecx (disti[v] = du+1)
    a.raw(b'\x8B\x0D' + le32(bfs_tail_va))               # ecx = tail
    a.raw(b'\x89\x04\x8D' + le32(bfs_queue_va))          # queue[tail] = v (eax)
    a.raw(b'\xFF\x05' + le32(bfs_tail_va))               # tail++
    a.label('bfr_edge_next')
    a.raw(b'\x46')                                        # inc esi
    a.jmp('bfr_edge_loop')

    a.label('bfr_bfs_done')
    a.label('bfr_next')
    a.raw(b'\xFF\x05' + le32(bfr_i_va))                  # i++
    a.jmp('bfr_loop')

    a.label('bfr_done')
    a.raw(b'\x61')                                        # popad
    a.raw(b'\xC3')

    # =====================================================================
    # ctf_pick_goal: set route_goal_flag = this bot's goal flag index (the HOME
    # base if carrying, else the ENEMY base; -1 when routing inactive / no goal).
    # Reads bot_slot_tmp / bot_char_tmp / bot_team. Called per-frame by the
    # follower (final-approach check) and by ctf_next_hop. Clobbers GPRs; carry
    # is spilled to route_carry so it survives the sub_4267E0/sub_425290 calls.
    # =====================================================================
    a.label('ctf_pick_goal')
    a.raw(b'\xC7\x05' + le32(route_goal_va) + le32(0xFFFFFFFF))  # route_goal_flag = -1
    a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')       # routing active?
    a.jz('cpg_done')
    # Per-bot routing suspension: after a routed progress-timeout the follower
    # parks BFS routing for WP_ROUTE_SUSPEND_FRAMES (see bot_movement.py) so
    # the bot roams instead of being funnelled back into a blocked segment.
    # Reporting "no goal" here suspends the next-hop bias, the final approach
    # AND the far-base force-tick in one place. The counter is decremented
    # once per think by the follower; this is a pure read.
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                      # ecx = slot
    a.raw(b'\x83\x3C\x8D' + le32(route_suspend_va) + b'\x00')   # suspended?
    a.jz('cpg_not_suspended')
    a.raw(b'\xC7\x05' + le32(route_carry_va) + le32(0))         # keep global fresh
    a.jmp('cpg_done')
    a.label('cpg_not_suspended')
    # carrying? -> route_carry (live-verified inventory-group test)
    a.raw(b'\xC7\x05' + le32(route_carry_va) + le32(0))         # route_carry = 0
    a.raw(b'\x8B\x0D' + le32(bot_char_va))                      # ecx = bot char
    a.raw(b'\x85\xC9'); a.jz('cpg_carry_done')                  # NULL char
    a.call_va(ax.SUB_4267E0_VA)                                  # eax = inv (ret 0)
    a.raw(b'\x85\xC0'); a.jz('cpg_carry_done')                  # NULL inv
    a.raw(b'\x8B\x15' + le32(ax.MULTIPLAYER_FLAG_GID_VA))       # edx = FLAG_GID
    a.raw(b'\x85\xD2'); a.jz('cpg_carry_done')                  # gid unresolved
    a.raw(b'\x52')                                              # push gid
    a.raw(b'\x89\xC1')                                          # ecx = inv
    a.call_va(ax.SUB_425290_VA)                                  # eax = slot (ret 4)
    a.raw(b'\x83\xF8\xFF'); a.jz('cpg_carry_done')             # slot == -1 -> not carrying
    a.raw(b'\xC7\x05' + le32(route_carry_va) + le32(1))        # route_carry = 1
    a.label('cpg_carry_done')
    # team -> ebx (no engine calls after here)
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x8B\x1C\x8D' + le32(bot_team_va))                 # ebx = bot_team[slot]
    a.raw(b'\x8B\x0D' + le32(flag_count_va))                   # ecx = flag_count
    a.raw(b'\x83\xF9' + bytes([RMAX]))                         # cmp ecx, RMAX
    a.jbe('cpg_nb_ok')
    a.raw(b'\xB9' + le32(RMAX))
    a.label('cpg_nb_ok')
    a.raw(b'\x85\xC9'); a.jz('cpg_done')                       # no bases
    a.raw(b'\x31\xF6')                                         # esi = 0 (i)
    a.raw(b'\xBF\xFF\xFF\xFF\xFF')                             # edi = -1 (home)
    a.raw(b'\xBA\xFF\xFF\xFF\xFF')                             # edx = -1 (enemy)
    a.label('cpg_goal_loop')
    a.raw(b'\x39\xCE'); a.jae('cpg_goal_done')                 # i >= nbase
    a.raw(b'\x8B\x04\xB5' + le32(flag_team_va))               # eax = flag_team[i]
    a.raw(b'\x39\xD8'); a.jnz('cpg_goal_enemy')               # != team -> enemy
    a.raw(b'\x83\xFF\xFF'); a.jnz('cpg_goal_next')            # home already set
    a.raw(b'\x89\xF7'); a.jmp('cpg_goal_next')                # home = i
    a.label('cpg_goal_enemy')
    a.raw(b'\x83\xFA\xFF'); a.jnz('cpg_goal_next')            # enemy already set
    a.raw(b'\x89\xF2')                                         # enemy = i
    a.label('cpg_goal_next')
    a.raw(b'\x46'); a.jmp('cpg_goal_loop')                     # ++i
    a.label('cpg_goal_done')
    a.raw(b'\x83\x3D' + le32(route_carry_va) + b'\x00')        # carrying?
    a.jz('cpg_pick_enemy')
    a.raw(b'\x89\xF8')                                         # eax = home (edi)
    a.jmp('cpg_store')
    a.label('cpg_pick_enemy')
    a.raw(b'\x89\xD0')                                         # eax = enemy (edx)
    a.label('cpg_store')
    a.raw(b'\x83\xF8\xFF'); a.jz('cpg_store_goal')             # no goal -> store -1
    a.raw(b'\x83\x3C\x85' + le32(flag_present_va) + b'\x00')  # cmp flag_present[goal], 0
    a.jz('cpg_goal_missing')
    # Goal flag is present at base: clear any missing-flag policy for this bot.
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(missing_policy_va) + le32(0))
    a.raw(b'\xC7\x04\x8D' + le32(missing_goal_va) + le32(0xFFFFFFFF))
    a.jmp('cpg_store_goal')

    a.label('cpg_goal_missing')
    # If we are carrying the enemy flag, the missing goal is our OWN home flag.
    # Do not route/final-approach to an empty home base: normal CTF forbids a
    # capture while our flag is away, and the page-flip far-base tick can wake
    # capture entities that would otherwise stay camera-gated. Search instead.
    a.raw(b'\x83\x3D' + le32(route_carry_va) + b'\x00')        # carrying?
    a.jz('cpg_missing_attacker')
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(missing_policy_va) + le32(1)) # policy = search
    a.raw(b'\x89\x04\x8D' + le32(missing_goal_va))             # missing_goal[slot] = goal
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # no goal -> random graph roam
    a.jmp('cpg_store_goal')

    a.label('cpg_missing_attacker')
    # The target flag is absent from its base. Keep the bot's policy stable
    # until this same target becomes present again or the bot switches goals.
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(missing_goal_va))             # edx = missing_goal[slot]
    a.raw(b'\x39\xC2')                                         # cmp edx, eax
    a.jnz('cpg_missing_roll')                                  # new missing goal -> re-roll
    a.raw(b'\x83\x3C\x8D' + le32(missing_policy_va) + b'\x00') # cmp missing_policy[slot], 0
    a.jz('cpg_missing_roll')                                   # unset -> roll
    a.jmp('cpg_missing_have_policy')

    a.label('cpg_missing_roll')
    a.raw(b'\x89\x04\x8D' + le32(missing_goal_va))             # missing_goal[slot] = goal
    a.raw(b'\x6A\x01')                                         # push high=1
    a.raw(b'\x6A\x00')                                         # push low=0
    a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                       # ecx = RNG
    a.call_va(ax.RNG_SUB)                                      # eax = 0/1 (callee pops args)
    a.raw(b'\x40')                                             # policy = eax + 1 (1 search, 2 wait)
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x89\x04\x8D' + le32(missing_policy_va))           # missing_policy[slot] = policy
    a.raw(b'\x8B\x04\x8D' + le32(missing_goal_va))             # eax = goal

    a.label('cpg_missing_have_policy')
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\x8B\x14\x8D' + le32(missing_policy_va))           # edx = policy
    a.raw(b'\x83\xFA\x01')                                     # policy == search?
    a.jnz('cpg_store_goal')                                    # wait -> keep eax=goal
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                             # search -> random graph roam

    a.label('cpg_store_goal')
    a.raw(b'\xA3' + le32(route_goal_va))                       # route_goal_flag = eax (or -1)
    a.label('cpg_done')
    a.raw(b'\xC3')

    # =====================================================================
    # ctf_next_hop(ECX = current node idx) -> EAX = goal-biased next node,
    # or 0xFFFFFFFF when routing can't apply (caller falls back to wp_advance).
    # Reads bot_slot_tmp / bot_char_tmp / bot_team from scratch. Called inside
    # the bot-movement pushad frame, so it may clobber any GPR.
    # =====================================================================
    a.label('ctf_next_hop')
    a.raw(b'\x89\x0D' + le32(route_cur_va))               # route_cur = ecx (cur)
    a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00') # cmp [flag_routing_active], 0
    a.jz('cnh_fail')
    a.call_lbl('ctf_pick_goal')                           # sets route_goal_flag
    a.raw(b'\xA1' + le32(route_goal_va))                  # eax = goal flag idx
    a.raw(b'\x83\xF8\xFF'); a.jz('cnh_fail')             # no goal base

    # disti = flag_dist + goal*ROW -> EBP
    a.raw(b'\x69\xC0' + le32(ROW))                       # imul eax, eax, ROW
    a.raw(b'\x05' + le32(flag_dist_va))                 # add eax, flag_dist
    a.raw(b'\x89\xC5')                                   # ebp = disti
    # cur -> EBX; cur_d = disti[cur] -> ECX (best-distance threshold)
    a.raw(b'\x8B\x1D' + le32(route_cur_va))             # ebx = cur
    a.raw(b'\x8B\x4C\x9D\x00')                           # ecx = [ebp + ebx*4] (cur_d)
    a.raw(b'\x83\xF9\xFF')                               # cmp ecx, -1 (cur unreachable?)
    a.jz('cnh_fail')
    a.raw(b'\xBA\xFF\xFF\xFF\xFF')                       # edx = -1 (best)
    a.raw(b'\x31\xF6')                                   # esi = 0 (edge idx)

    a.label('cnh_scan_loop')
    a.raw(b'\x3B\x35' + le32(ecount_va))                # cmp esi, edge_count
    a.jae('cnh_scan_done')
    a.raw(b'\x8B\x04\xB5' + le32(edges_va))            # eax = edges[esi]
    a.raw(b'\x0F\xB7\xF8')                              # movzx edi, ax (i)
    a.raw(b'\xC1\xE8\x10')                              # shr eax, 16   (j)
    a.raw(b'\x39\xDF')                                  # cmp edi, ebx (i == cur?)
    a.jz('cnh_nb_j')                                    # nb = j (eax)
    a.raw(b'\x39\xD8')                                  # cmp eax, ebx (j == cur?)
    a.jz('cnh_nb_i')                                    # nb = i (edi)
    a.jmp('cnh_scan_next')
    a.label('cnh_nb_i')
    a.raw(b'\x89\xF8')                                  # eax = edi (nb = i)
    a.label('cnh_nb_j')                                # nb in eax
    a.raw(b'\x3B\x05' + le32(vcount_va))               # cmp eax, vertex_count
    a.jae('cnh_scan_next')                             # out of range
    a.raw(b'\x8B\x7C\x85\x00')                          # edi = [ebp + eax*4] (nd = disti[nb])
    a.raw(b'\x39\xCF')                                  # cmp edi, ecx (nd - best_d)
    a.jae('cnh_scan_next')                             # nd >= best_d -> not closer
    a.raw(b'\x89\xF9')                                  # ecx = edi (best_d = nd)
    a.raw(b'\x89\xC2')                                  # edx = eax (best = nb)
    a.label('cnh_scan_next')
    a.raw(b'\x46')                                      # inc esi
    a.jmp('cnh_scan_loop')

    a.label('cnh_scan_done')
    a.raw(b'\x89\xD0')                                  # eax = edx (best, or -1)
    a.raw(b'\xC3')

    a.label('cnh_fail')
    a.raw(b'\xB8\xFF\xFF\xFF\xFF')                      # eax = -1
    a.raw(b'\xC3')
