"""``detour_df90`` for ``sub_59DF90``.

Captures a2 (``[esp+4]`` at entry) and detects match-change. ``cap_a2`` is a
per-match shared context (``sub_59BD50`` passes the same a2 for every player
in its setup loop), so a CHANGE in a2 between consecutive ``sub_59DF90`` calls
means a new match has started. When that happens we wipe the four bot scratch
arrays (participants/indices/chars/controllers — 64 contiguous dwords at
``scratch+0x180..0x280``) so leftover match-1 pointers don't falsely match
newly-allocated match-2 objects. (This was Bug 1: stale
``bot_controllers_va`` entries made ``detour_542360`` zero the host's
movement vector in subsequent matches.)"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import AI_PERBOT_FIELD_COUNT, ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    cap_a2              = layout.va('cap_a2')
    bot_participants_va = layout.va('bot_participants')
    bot_team_va         = layout.va('bot_team')
    host_part_va        = layout.va('host_part')
    used_names_va       = layout.va('used_names')
    wander_x_va         = layout.va('bot_wander_x')
    bot_current_wp_va   = layout.va('bot_current_wp')
    bot_pickup_valid_va = layout.va('bot_pickup_valid')
    hazard_count_va     = layout.va('hazard_count')
    frame_counter_va    = layout.va('frame_counter')
    pickup_count_va     = layout.va('pickup_count')
    pickup_last_frame_va = layout.va('pickup_last_frame')

    a.label('detour_df90')
    a.raw(b'\x50')                                # push eax
    a.raw(b'\x8B\x44\x24\x08')                    # mov eax, [esp+8] (incoming a2)
    a.raw(b'\x3B\x05' + le32(cap_a2))             # cmp eax, [cap_a2]
    a.jz('df90_same_match')
    # New match: clear 64 contiguous dwords at bot_participants..bot_controllers,
    # then init the team-cache tables to -1 (sentinel "unknown team").
    a.raw(b'\x57\x51\x50')                        # push edi; push ecx; push eax
    a.raw(b'\xBF' + le32(bot_participants_va))    # mov edi, bot_participants_va
    a.raw(b'\xB9\x40\x00\x00\x00')                # mov ecx, 64
    a.raw(b'\x31\xC0')                            # xor eax, eax
    a.raw(b'\xFC')                                # cld
    a.raw(b'\xF3\xAB')                            # rep stosd
    a.raw(b'\xBF' + le32(bot_team_va))            # mov edi, bot_team_va
    a.raw(b'\xB9\x10\x00\x00\x00')                # mov ecx, 16  (bot_team[16])
    a.raw(b'\x83\xC8\xFF')                        # or eax, -1
    a.raw(b'\xF3\xAB')                            # rep stosd
    # Clear all per-bot AI state (AI_PERBOT_FIELD_COUNT contiguous parallel u32
    # arrays starting at bot_wander_x, each 16 entries × 4 bytes = 64 bytes).
    # This wipes wander targets, stuck counters, item-scan stagger, pickup
    # caches, last_damage, flee_ticks AND the waypoint-follow nav fields
    # (current_wp, prev_wp, wp_try) so a fresh match starts clean. (wp_try
    # counts frames since last node arrival; 0 is the correct fresh value.)
    # Count is derived from the layout constant so appending an AI field there
    # extends this clear automatically (tests pin the count + ordering).
    a.raw(b'\xBF' + le32(wander_x_va))            # mov edi, bot_wander_x
    a.raw(b'\xB9' + le32(AI_PERBOT_FIELD_COUNT * cfg.MAX_BOT_SLOTS))  # ecx = fields * 16 slots dwords
    a.raw(b'\x31\xC0')                            # xor eax, eax
    a.raw(b'\xF3\xAB')                            # rep stosd
    # The two follow-nav index arrays (bot_current_wp, bot_prev_wp — the last
    # two fields, contiguous) must start at -1, not 0: a zero prev_wp would
    # falsely claim "latched on vertex 0" and a zero current_wp would skip the
    # cold-acquire. Re-stamp them to 0xFFFFFFFF (32 dwords = both arrays).
    a.raw(b'\xBF' + le32(bot_current_wp_va))      # mov edi, bot_current_wp
    a.raw(b'\xB9' + le32(2 * cfg.MAX_BOT_SLOTS))  # ecx = current_wp + prev_wp dwords
    a.raw(b'\x83\xC8\xFF')                        # or eax, -1
    a.raw(b'\xF3\xAB')                            # rep stosd
    # Reset frame counter so per-bot scan-stagger math doesn't see a fake
    # huge delta on the first frame of the new match.
    a.raw(b'\xC7\x05' + le32(frame_counter_va) + le32(0))
    # Drop stale pickup overlay markers from the previous match/map. The
    # registration detour repopulates this on the next enabled pickup tick.
    a.raw(b'\xC7\x05' + le32(pickup_count_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(pickup_last_frame_va) + le32(0))
    # Clear cached host participant ptr to NULL so the next match's first
    # spawn re-captures it. Must be 0 (not -1) so fire/aim's `test eax`
    # guard works without dereferencing a bogus pointer.
    a.raw(b'\xC7\x05' + le32(host_part_va) + le32(0))
    # Reset the per-match "name claimed" bitmap so every bot name is
    # available again. Without this, names claimed in match N would stay
    # marked-as-used across to match N+1, eventually starving the linear
    # search in spawn.py and forcing duplicate name re-use.
    a.raw(b'\xBF' + le32(used_names_va))          # mov edi, used_names_va
    a.raw(b'\xB9' + le32(cfg.NUM_BOT_NAMES))      # mov ecx, NUM_BOT_NAMES
    a.raw(b'\x31\xC0')                            # xor eax, eax
    a.raw(b'\xF3\xAA')                            # rep stosb
    # Once we've pre-grown mgr+0x290 to 16 entries (match 1 onwards), the
    # buffer outlives the match: slots populated by match N can still hold
    # freed char pointers at the start of match N+1. Per-frame fire/aim
    # iterates the array and calls sub_4FB0A0 on every non-NULL slot, so any
    # stale pointer crashes via its (now invalid) vtable. Clear all 16 slots
    # to NULL on match change. Only the engine's sub_59DF90 (which we're the
    # prologue of) refills them, so this is consistent with the engine's own
    # "match started" state.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))   # mov eax, [mgr]
    a.raw(b'\x85\xC0'); a.jz('df90_skip_mgr_clear')
    a.raw(b'\x83\xB8\x98\x02\x00\x00\x10')        # cmp [eax+0x298], 16
    a.jb('df90_skip_mgr_clear')                   # capacity not pre-grown yet
    a.raw(b'\x8B\xB8\x90\x02\x00\x00')            # mov edi, [eax+0x290]
    a.raw(b'\x85\xFF'); a.jz('df90_skip_mgr_clear')
    a.raw(b'\xB9\x10\x00\x00\x00')                # mov ecx, 16
    a.raw(b'\x31\xC0')                            # xor eax, eax
    a.raw(b'\xF3\xAB')                            # rep stosd
    a.label('df90_skip_mgr_clear')
    # Rebuild the hazard cache. scan_hazards is __cdecl-ish (pushad/popad,
    # no args, no return) and self-clears hazard_count internally, so
    # calling it unconditionally on every match change is safe even if
    # the world manager is briefly NULL.
    a.call_lbl('scan_hazards')
    # Auto-load this map's saved waypoint graph. wp_load is pushad/popad,
    # no args, no return — silent on missing file (counts left at 0 = empty
    # graph). Map-name CString (`dword_713C14`) is populated by sub_4F43F0
    # before any sub_59DF90 call, so it's safe to read here.
    a.call_lbl('wp_load')
    # Copy this map's build-time parsed teleport destinations into the live
    # overlay table. This is cheap and bounded: a fixed static map-name table
    # plus a small float[2] copy, no heap scan.
    a.call_lbl('load_portals')
    # Bind every live pad (and its resolved teleport destination) to its
    # nearest graph node so bfs_run can traverse pads as directed edges and
    # the roam wander-entry can recognise pad nodes. Needs the graph, so it
    # runs after wp_load; needs the pads, so after load_portals; and it must
    # precede build_flag_routes below. Inert stub on non-portal-route builds.
    a.call_lbl('bind_portal_nodes')
    # Copy this map's build-time parsed CTF flag-base anchors into the live
    # flag_table (overlay markers + future CTF bot routing). Same cheap bounded
    # static-table copy as load_portals; inert stub on non-flag builds.
    a.call_lbl('load_flags')
    # Copy this map's build-time parsed door centers into the live door_table
    # and reset the live door state (anchor-entity cache, door_blocked[],
    # per-bot wedge latches, dirty/cooldown). Inert stub on non-door builds.
    # The periodic grid scan (seeded below) fills the entity cache within ~1
    # frame; the page-flip per-frame refresh derives door_blocked[] from it.
    a.call_lbl('load_doors')
    # Copy this map's build-time parsed switch centers, class bytes and
    # (switch, door) pair records into the live tables. Pair door indices
    # reference the door_table order load_doors just filled — keep this call
    # AFTER load_doors. Inert stub on non-switch builds.
    a.call_lbl('load_switches')
    # Static edge->door adjacency for the door-aware routing field. Doors and
    # the graph never move mid-match, so the point-segment sweep runs once
    # here (wp_load above loaded the graph; load_doors filled door_table).
    a.call_lbl('build_edge_doors')
    # Quantized physical edge lengths — the per-edge traversal cost every
    # bfs_run field (full/open/seek/drop) adds since the weighted-SPFA
    # change. Graph is static per match; runs once here after wp_load.
    # Inert stub on non-routing builds.
    a.call_lbl('build_edge_lens')
    # CTF flag routing: when the active match is CTF with a graph + flags,
    # precompute the per-base BFS distance field (build_flag_routes) and arm the
    # runtime gate (flag_routing_active). detect_mode returns 0/1/2 (DM/CTF/SK);
    # only CTF (1) routes. Otherwise the gate stays 0 and bots roam randomly.
    if cfg.CTF_FLAG_ROUTING_ENABLED and layout.has_field('flag_routing_active'):
        a.raw(b'\xC7\x05' + le32(layout.va('flag_routing_active')) + le32(0))
        if layout.has_field('route_missing_policy') and layout.has_field('route_missing_goal'):
            a.raw(b'\xFC')                                # cld
            a.raw(b'\xBF' + le32(layout.va('route_missing_policy')))
            a.raw(b'\xB9' + le32(cfg.MAX_BOT_SLOTS))
            a.raw(b'\x31\xC0')
            a.raw(b'\xF3\xAB')                         # route_missing_policy[16] = 0
            a.raw(b'\xBF' + le32(layout.va('route_missing_goal')))
            a.raw(b'\xB9' + le32(cfg.MAX_BOT_SLOTS))
            a.raw(b'\x83\xC8\xFF')
            a.raw(b'\xF3\xAB')                         # route_missing_goal[16] = -1
        # Reset the door-change route-epoch. Both the global and every bot's
        # stored epoch start at 0 so bots do NOT force a re-acquire at match
        # start (0 == 0); the first mid-life door rebuild bumps the global and
        # the mismatch drives a one-shot re-acquire per bot.
        if layout.has_field('route_epoch') and layout.has_field('bot_route_epoch'):
            a.raw(b'\xC7\x05' + le32(layout.va('route_epoch')) + le32(0))
            a.raw(b'\xFC')                            # cld
            a.raw(b'\xBF' + le32(layout.va('bot_route_epoch')))
            a.raw(b'\xB9' + le32(cfg.MAX_BOT_SLOTS))
            a.raw(b'\x31\xC0')
            a.raw(b'\xF3\xAB')                         # bot_route_epoch[16] = 0
        a.call_lbl('detect_mode')                    # eax = 0 DM / 1 CTF / 2 SK
        a.raw(b'\x83\xF8\x01')                        # cmp eax, 1 (CTF)
        a.jnz('df90_no_ctf_routes')
        a.raw(b'\x83\x3D' + le32(layout.va('flag_count')) + b'\x00')  # flags present?
        a.jz('df90_no_ctf_routes')
        a.call_lbl('build_flag_routes')              # per-match BFS distance field
        a.raw(b'\xC7\x05' + le32(layout.va('flag_routing_active')) + le32(1))
        a.label('df90_no_ctf_routes')
    # Capture the active map's CPlasmaTileMap* (lava) for proactive avoidance.
    # pushad/popad, no args/ret; self-clears plasma_map (0 on non-plasma maps).
    a.call_lbl('scan_plasma')
    # Enumerate the world's entities into scan_table (general spatial-grid walk)
    # so object detection / portal active-state can read live entities. pushad/
    # popad, no args/ret. Gated at build time — when disabled the call is simply
    # not emitted. Once per match (match change), so it is not a hot path.
    if cfg.SCAN_ENTITIES_ENABLED:
        a.call_lbl('scan_diag')
    # Seed the per-portal active-state re-scan countdown so the page-flip detour
    # runs scan_portal_active shortly after the match starts (and every interval
    # after). 1 = fire on the next page flip.
    if cfg.PORTAL_ACTIVE_ENABLED and layout.has_field('portal_scan_count'):
        a.raw(b'\xC7\x05' + le32(layout.va('portal_scan_count')) + le32(1))
    a.raw(b'\x58\x59\x5F')                        # pop eax; pop ecx; pop edi
    a.label('df90_same_match')
    a.raw(b'\xA3' + le32(cap_a2))                 # mov [cap_a2], eax
    a.raw(b'\x58')                                # pop eax (caller's)
    a.raw(b'\x53')                                # push ebx (displaced prologue)
    a.raw(b'\x8B\x5C\x24\x0C')                    # mov ebx, [esp+0xC]
    a.jmp_va(ax.DF90_RESUME)
