"""``switch_seek_eval`` — page-flip switch-seek servicing: candidate
filter, one bounded team-gated BFS per frame, detour-metric scoring
and per-team activation of the winning door-opening switch."""

from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    routing_active_va = c.routing_active_va
    flag_dist_va = c.flag_dist_va
    bfs_disti_va = c.bfs_disti_va
    bfr_i_va = c.bfr_i_va
    vcount_va = c.vcount_va
    VMAX = c.VMAX
    RMAX = c.RMAX
    ROW = c.ROW
    door_mask_i_va = c.door_mask_i_va
    door_mask_j_va = c.door_mask_j_va
    door_blocked_va = c.door_blocked_va
    door_count_va = c.door_count_va
    bfs_start_va = c.bfs_start_va
    bfs_skip_va = c.bfs_skip_va
    seek = c.seek
    switch_node_va = c.switch_node_va
    switch_flags_va = c.switch_flags_va
    switch_pairs_va = c.switch_pairs_va
    switch_count_va = c.switch_count_va
    switch_pair_count_va = c.switch_pair_count_va
    seek_active_va = c.seek_active_va
    seek_node_va = c.seek_node_va
    seek_pending_va = c.seek_pending_va
    seek_req_node_va = c.seek_req_node_va
    seek_req_goal_va = c.seek_req_goal_va
    seek_tried_va = c.seek_tried_va
    seek_fail_va = c.seek_fail_va
    seek_timer_va = c.seek_timer_va
    seek_best_va = c.seek_best_va
    seek_best_score_va = c.seek_best_score_va
    seek_eval_s_va = c.seek_eval_s_va
    seek_req_open_va = c.seek_req_open_va
    seek_dist_va = c.seek_dist_va
    SEEK_ROW = c.SEEK_ROW

    # =====================================================================
    # switch_seek_eval: per-frame (page flip) seek servicing. For each team:
    # an ACTIVE seek ticks its timeout (expiry blacklists the switch until
    # the next door-state change); a PENDING request evaluates AT MOST ONE
    # candidate per frame — the untried door-opening switch with a currently
    # BLOCKED paired door, a bound node, and the smallest full-field distance
    # to the requester's goal — by running one team-door-gated bfs_run rooted
    # at the switch node into this team's seek_dist row. Requester reachable
    # => activate; unreachable => mark tried, next frame tries the next
    # candidate; none left => drop the request (bots keep today's fallback).
    # One BFS per frame keeps the page flip smooth. pushad/popad.
    # =====================================================================
    if not seek:
        a.label('switch_seek_eval')
        a.raw(b'\xC3')
    else:
        a.label('switch_seek_eval')
        a.raw(b'\x60')                                          # pushad
        a.raw(b'\x83\x3D' + le32(routing_active_va) + b'\x00')  # CTF routing live?
        a.jz('sse_out')
        a.raw(b'\x31\xED')                                      # ebp = 0 (team)

        a.label('sse_team_loop')
        a.raw(b'\x8B\x04\xAD' + le32(seek_active_va))           # eax = seek_active[team]
        a.raw(b'\x85\xC0')
        a.jz('sse_check_pending')
        # Active: tick the timeout.
        a.raw(b'\x8B\x0C\xAD' + le32(seek_timer_va))            # ecx = timer
        a.raw(b'\x85\xC9'); a.jz('sse_expire')                  # defensive
        a.raw(b'\x49')                                          # dec ecx
        a.raw(b'\x89\x0C\xAD' + le32(seek_timer_va))            # store back
        a.jnz('sse_next_team')                                  # still ticking
        a.label('sse_expire')
        a.raw(b'\x48')                                          # eax = switch idx
        a.raw(b'\x89\xC1')                                      # ecx = idx
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x09\x14\xAD' + le32(seek_fail_va))             # fail |= 1 << idx
        a.raw(b'\xC7\x04\xAD' + le32(seek_active_va) + le32(0))
        a.jmp('sse_next_team')

        a.label('sse_check_pending')
        a.raw(b'\x83\x3C\xAD' + le32(seek_pending_va) + b'\x00')
        a.jz('sse_next_team')
        # ---- find the FIRST untried viable candidate (cheap filters run in
        # one frame; the BFS below is at most ONE per frame). Every candidate
        # is scored by the DETOUR metric seek_walk(requester -> switch) +
        # full_dist(switch -> goal); the best is activated once all have been
        # evaluated. Cheap-rejects are marked tried immediately so the scan
        # never revisits them.
        a.raw(b'\x31\xF6')                                      # esi = 0 (s)
        a.label('sse_cand_loop')
        a.raw(b'\x3B\x35' + le32(switch_count_va))              # s >= switch_count?
        a.jae('sse_no_more')
        a.raw(b'\x83\xFE' + bytes([cfg.SWITCH_TABLE_MAX]))      # s >= table max?
        a.jae('sse_no_more')
        # tried/blacklisted?
        a.raw(b'\x89\xF1')                                      # ecx = s
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl (1<<s)
        a.raw(b'\x8B\x04\xAD' + le32(seek_tried_va))            # eax = tried[team]
        a.raw(b'\x0B\x04\xAD' + le32(seek_fail_va))             # eax |= fail[team]
        a.raw(b'\x85\xD0')                                      # test eax, edx
        a.jnz('sse_cand_next')
        # door-opening class?
        a.raw(b'\xF6\x04\x35' + le32(switch_flags_va) + b'\x01')  # flags[s] & OPENS?
        a.jz('sse_cand_tried')
        # node bound?
        a.raw(b'\x8B\x04\xB5' + le32(switch_node_va))           # eax = switch_node[s]
        a.raw(b'\x83\xF8\xFF')
        a.jz('sse_cand_tried')
        # any paired door currently blocked? (also the toggle-safety gate:
        # a toggler whose doors are all open must never be bumped shut)
        a.raw(b'\x31\xC9')                                      # ecx = 0 (pair idx)
        a.label('sse_pair_loop')
        a.raw(b'\x3B\x0D' + le32(switch_pair_count_va))         # pairs done?
        a.jae('sse_cand_tried')                                 # none blocked -> reject
        a.raw(b'\x8B\x14\x8D' + le32(switch_pairs_va))          # edx = pair record
        a.raw(b'\x0F\xB7\xC2')                                  # movzx eax, dx (switch idx)
        a.raw(b'\x39\xF0')                                      # cmp eax, esi
        a.jnz('sse_pair_next')
        a.raw(b'\xC1\xEA\x10')                                  # edx >>= 16 (door idx)
        a.raw(b'\x3B\x15' + le32(door_count_va))                # stale idx?
        a.jae('sse_pair_next')
        a.raw(b'\x83\x3C\x95' + le32(door_blocked_va) + b'\x00')
        a.jnz('sse_have_cand')
        a.label('sse_pair_next')
        a.raw(b'\x41')                                          # ++pair idx
        a.jmp('sse_pair_loop')
        a.label('sse_cand_tried')
        # Cheap reject: mark tried so tomorrow's scan skips it, keep scanning.
        a.raw(b'\x89\xF1')                                      # ecx = s
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x09\x14\xAD' + le32(seek_tried_va))            # tried |= 1<<s
        a.label('sse_cand_next')
        a.raw(b'\x46')                                          # ++s
        a.jmp('sse_cand_loop')

        a.label('sse_have_cand')
        # One team-gated BFS rooted at candidate esi's node into seek_dist[team].
        a.raw(b'\x89\x35' + le32(seek_eval_s_va))               # spill candidate s (NOT bfs_u: bfs_run clobbers it)
        a.raw(b'\x89\xE8')                                      # eax = team
        a.raw(b'\x69\xC0' + le32(SEEK_ROW))                     # eax *= SEEK_ROW
        a.raw(b'\x05' + le32(seek_dist_va))                     # eax = seek row
        a.raw(b'\xA3' + le32(bfs_disti_va))                     # bfs_disti = row
        a.raw(b'\x89\xC7')                                      # edi = row
        a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd (clear row)
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(1))        # skip blocked edges
        a.raw(b'\x8D\x4C\x2D\x00')                              # lea ecx, [ebp+ebp] (team*2)
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x89\x15' + le32(door_mask_i_va))               # door_mask_i = 1<<team*2
        a.raw(b'\x01\xD2')                                      # edx += edx
        a.raw(b'\x89\x15' + le32(door_mask_j_va))               # door_mask_j = 2<<team*2
        a.raw(b'\xA1' + le32(seek_eval_s_va))                   # eax = candidate s
        a.raw(b'\x8B\x04\x85' + le32(switch_node_va))           # eax = switch_node[s]
        a.raw(b'\xA3' + le32(bfs_start_va))                     # bfs_start = node
        a.raw(b'\x89\x2D' + le32(bfr_i_va))                     # spill team (bfs clobbers GPRs)
        a.call_lbl('bfs_run')
        a.raw(b'\x8B\x2D' + le32(bfr_i_va))                     # ebp = team
        # Mark tried regardless of the outcome.
        a.raw(b'\x8B\x0D' + le32(seek_eval_s_va))               # ecx = candidate s
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x09\x14\xAD' + le32(seek_tried_va))            # tried |= 1<<s
        # Requester reachable?
        a.raw(b'\x8B\x04\xAD' + le32(seek_req_node_va))         # eax = req node
        a.raw(b'\x3B\x05' + le32(vcount_va))                    # defensive range
        a.jae('sse_next_team')
        a.raw(b'\x89\xE9')                                      # ecx = team
        a.raw(b'\x69\xC9' + le32(SEEK_ROW))                     # ecx *= SEEK_ROW
        a.raw(b'\x81\xC1' + le32(seek_dist_va))                 # ecx = seek row
        a.raw(b'\x8B\x0C\x81')                                  # ecx = seek_dist[req node]
        a.raw(b'\x83\xF9\xFF')
        a.jz('sse_next_team')                                   # unreachable -> next frame
        # score2 = seek walk + full_dist(switch_node -> req_goal)
        a.raw(b'\x8B\x04\xAD' + le32(seek_req_goal_va))         # eax = req_goal
        a.raw(b'\x83\xF8' + bytes([RMAX]))                      # defensive range
        a.jae('sse_next_team')
        a.raw(b'\x69\xC0' + le32(ROW))                          # eax *= ROW
        a.raw(b'\x05' + le32(flag_dist_va))                     # eax = full row base
        a.raw(b'\x8B\x15' + le32(seek_eval_s_va))               # edx = candidate s
        a.raw(b'\x8B\x14\x95' + le32(switch_node_va))           # edx = switch_node[s]
        a.raw(b'\x8B\x04\x90')                                  # eax = full[switch node]
        a.raw(b'\x83\xF8\xFF')                                  # goal unreachable from switch?
        a.jz('sse_next_team')
        a.raw(b'\x01\xC1')                                      # ecx += eax (score2)
        # BENEFIT bar: the switch route (walk + ideal post-open path) must
        # BEAT the requester's current open route, else the detour is a net
        # loss and the candidate is skipped. req_open == -1 (goal open-field
        # unreachable, e.g. sealed Torture bases) accepts any viable switch.
        a.raw(b'\x8B\x04\xAD' + le32(seek_req_open_va))         # eax = req_open[team]
        a.raw(b'\x83\xF8\xFF')
        a.jz('sse_benefit_ok')
        a.raw(b'\x39\xC1')                                      # cmp score2, req_open
        a.jae('sse_next_team')                                  # no gain -> skip candidate
        a.label('sse_benefit_ok')
        # Better than the round's best so far? (best==0 = none yet)
        a.raw(b'\x8B\x04\xAD' + le32(seek_best_va))             # eax = best idx+1
        a.raw(b'\x85\xC0'); a.jz('sse_take')
        a.raw(b'\x3B\x0C\xAD' + le32(seek_best_score_va))       # cmp score2, best score
        a.jae('sse_next_team')
        a.label('sse_take')
        a.raw(b'\x8B\x15' + le32(seek_eval_s_va))               # edx = candidate s
        a.raw(b'\x42')                                          # edx = s+1
        a.raw(b'\x89\x14\xAD' + le32(seek_best_va))             # best = s+1
        a.raw(b'\x89\x0C\xAD' + le32(seek_best_score_va))       # best score = score2
        a.jmp('sse_next_team')

        a.label('sse_no_more')
        # Every candidate evaluated: activate the round's best, or drop.
        a.raw(b'\x8B\x04\xAD' + le32(seek_best_va))             # eax = best idx+1
        a.raw(b'\x85\xC0'); a.jz('sse_drop')
        a.raw(b'\x48')                                          # eax = best idx
        a.raw(b'\xA3' + le32(seek_eval_s_va))                   # spill (survives bfs_run)
        # Re-run the winner's BFS (the row currently holds the LAST candidate).
        a.raw(b'\x89\xE8')                                      # eax = team
        a.raw(b'\x69\xC0' + le32(SEEK_ROW))                     # eax *= SEEK_ROW
        a.raw(b'\x05' + le32(seek_dist_va))                     # eax = seek row
        a.raw(b'\xA3' + le32(bfs_disti_va))                     # bfs_disti = row
        a.raw(b'\x89\xC7')                                      # edi = row
        a.raw(b'\xB9' + le32(VMAX))                             # ecx = VMAX
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                          # eax = -1
        a.raw(b'\xFC\xF3\xAB')                                  # cld; rep stosd
        a.raw(b'\xC7\x05' + le32(bfs_skip_va) + le32(1))
        a.raw(b'\x8D\x4C\x2D\x00')                              # lea ecx, [ebp+ebp]
        a.raw(b'\xBA\x01\x00\x00\x00')                          # edx = 1
        a.raw(b'\xD3\xE2')                                      # shl edx, cl
        a.raw(b'\x89\x15' + le32(door_mask_i_va))
        a.raw(b'\x01\xD2')
        a.raw(b'\x89\x15' + le32(door_mask_j_va))
        a.raw(b'\xA1' + le32(seek_eval_s_va))                   # eax = winner s
        a.raw(b'\x8B\x04\x85' + le32(switch_node_va))           # eax = its node
        a.raw(b'\xA3' + le32(bfs_start_va))
        a.raw(b'\x89\x2D' + le32(bfr_i_va))                     # spill team
        a.call_lbl('bfs_run')
        a.raw(b'\x8B\x2D' + le32(bfr_i_va))                     # ebp = team
        # ACTIVATE.
        a.raw(b'\xA1' + le32(seek_eval_s_va))                   # eax = winner s
        a.raw(b'\x8B\x0C\x85' + le32(switch_node_va))           # ecx = its node
        a.raw(b'\x89\x0C\xAD' + le32(seek_node_va))             # seek_node[team] = node
        a.raw(b'\x40')                                          # eax = s+1
        a.raw(b'\x89\x04\xAD' + le32(seek_active_va))           # seek_active[team] = s+1
        a.raw(b'\xC7\x04\xAD' + le32(seek_timer_va)
              + le32(cfg.SWITCH_SEEK_TIMEOUT_FRAMES))
        a.label('sse_drop')
        a.raw(b'\xC7\x04\xAD' + le32(seek_pending_va) + le32(0))
        a.raw(b'\xC7\x04\xAD' + le32(seek_tried_va) + le32(0))
        a.raw(b'\xC7\x04\xAD' + le32(seek_best_va) + le32(0))
        a.raw(b'\xC7\x04\xAD' + le32(seek_best_score_va) + le32(0))

        a.label('sse_next_team')
        a.raw(b'\x45')                                          # ++team
        a.raw(b'\x83\xFD\x02')                                  # team < 2?
        a.jb('sse_team_loop')
        a.label('sse_out')
        a.raw(b'\x61')                                          # popad
        a.raw(b'\xC3')

