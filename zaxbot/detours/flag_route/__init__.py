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

from ...asm import Asm
from ...layout import ScratchLayout
from ._ctx import build_ctx
from . import drop, fields, goal, next_hop, seek, sk_routes


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
        a.label('build_edge_lens'); a.raw(b'\xC3')
        a.label('rebuild_open_routes'); a.raw(b'\xC3')
        a.label('ctf_pick_goal'); a.raw(b'\xC3')
        a.label('ctf_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')  # mov eax,-1; ret
        a.label('switch_seek_eval'); a.raw(b'\xC3')
        a.label('drop_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')
        a.label('drop_route_refresh'); a.raw(b'\xC3')
        a.label('build_sk_routes'); a.raw(b'\xC3')
        a.label('sk_update_phase'); a.raw(b'\xC3')
        a.label('sk_next_hop'); a.raw(b'\xB8\xFF\xFF\xFF\xFF\xC3')
        a.label('build_item_routes'); a.raw(b'\xC3')
        a.label('sk_pile_route_refresh'); a.raw(b'\xC3')
        return

    c = build_ctx(layout)
    fields.emit(a, layout, c)
    goal.emit(a, layout, c)
    next_hop.emit(a, layout, c)
    seek.emit(a, layout, c)
    drop.emit(a, layout, c)
    sk_routes.emit(a, layout, c)
