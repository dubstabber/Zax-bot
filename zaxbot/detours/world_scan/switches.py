"""``load_switches`` (per-match centers/classes/pairs),
``switch_blocked_census`` (paired-door blocked count) and
``switch_wander_check`` (roam-time wander-bump roll + latch)."""

from ... import addresses as ax
from ... import config as cfg
from ... import door_data
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    # =====================================================================
    # load_switches: copy the build-time switch centers, class bytes and
    # (switch, door) pair records for the active map into the live tables.
    # Called once per match from detour_df90 right after load_doors (pair
    # door indices reference the same map's door_table order, so both copies
    # must come from the same parse — they do, both static tables pack in
    # parse order). Same bounded map-name match as load_doors. Inert stub
    # when the switch layout fields are absent.
    # =====================================================================
    if not (
        layout.has_field('switch_table')
        and layout.has_field('switch_flags')
        and layout.has_field('switch_pairs')
        and layout.has_field('switch_static_maps')
        and layout.has_field('switch_static_points')
        and layout.has_field('switch_static_flags')
        and layout.has_field('switch_static_pairs')
    ):
        a.label('load_switches')
        a.raw(b'\xC3')
    else:
        switch_count_va         = layout.va('switch_count')
        switch_table_va         = layout.va('switch_table')
        switch_flags_va         = layout.va('switch_flags')
        switch_pair_count_va    = layout.va('switch_pair_count')
        switch_pairs_va         = layout.va('switch_pairs')
        switch_static_map_count_va = layout.va('switch_static_map_count')
        switch_static_maps_va   = layout.va('switch_static_maps')
        switch_static_points_va = layout.va('switch_static_points')
        switch_static_flags_va  = layout.va('switch_static_flags')
        switch_static_pairs_va  = layout.va('switch_static_pairs')
        switch_map_stride       = cfg.SWITCH_MAP_NAME_SLOT + 16

        a.label('load_switches')
        a.raw(b'\x60')                                              # pushad
        a.raw(b'\xC7\x05' + le32(switch_count_va) + le32(0))        # switch_count = 0
        a.raw(b'\xC7\x05' + le32(switch_pair_count_va) + le32(0))   # pair_count = 0
        a.raw(b'\xBF' + le32(switch_table_va))                      # edi = switch_table
        a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX * 2))             # ecx = table dwords
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xFC\xF3\xAB')                                      # cld; rep stosd
        a.raw(b'\xBF' + le32(switch_flags_va))                      # edi = switch_flags
        a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX))                 # ecx = flag bytes
        a.raw(b'\xF3\xAA')                                          # rep stosb (eax still 0)
        if layout.has_field('switch_node') and layout.has_field('bot_switch_target'):
            # Per-match wander-bump state: switch_node unbound until the bind
            # loop below (build_flag_routes also binds, but only on CTF
            # matches — DM roamers need the binding too); the per-bot
            # latch/cooldown/patience/census arrays are contiguous, one clear
            # covers all four.
            a.raw(b'\xBF' + le32(layout.va('switch_node')))         # edi = switch_node
            a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX))             # ecx = table max
            a.raw(b'\x83\xC8\xFF')                                  # eax = -1
            a.raw(b'\xF3\xAB')                                      # rep stosd
            a.raw(b'\xBF' + le32(layout.va('bot_switch_target')))   # edi = per-bot block
            a.raw(b'\xB9' + le32(4 * cfg.MAX_BOT_SLOTS))            # target+cd+try+snap
            a.raw(b'\x31\xC0')                                      # eax = 0
            a.raw(b'\xF3\xAB')                                      # rep stosd
        a.raw(b'\xA1' + le32(ax.MAP_NAME_CSTRING_VA))               # eax = [map CString header]
        a.raw(b'\x85\xC0'); a.jz('lsw_done')
        a.raw(b'\x83\xC0' + bytes([ax.MAP_NAME_ASCII_OFFSET]))      # eax = map name ASCII
        a.raw(b'\x80\x38\x00'); a.jz('lsw_done')                    # empty name?
        a.raw(b'\x89\xC5')                                          # ebp = active map name

        a.raw(b'\x8B\x0D' + le32(switch_static_map_count_va))       # ecx = map_count
        a.raw(b'\x85\xC9'); a.jz('lsw_done')
        a.raw(b'\x83\xF9' + bytes([cfg.SWITCH_STATIC_MAP_MAX]))     # cmp ecx, static max
        a.jbe('lsw_map_count_ok')
        a.raw(b'\xB9' + le32(cfg.SWITCH_STATIC_MAP_MAX))            # cap corrupt count defensively
        a.label('lsw_map_count_ok')
        a.raw(b'\x31\xF6')                                          # esi = map idx

        a.label('lsw_map_loop')
        a.raw(b'\x39\xCE'); a.jae('lsw_done')                       # if idx >= map_count
        a.raw(b'\x69\xC6' + le32(switch_map_stride))                # eax = idx * map_stride
        a.raw(b'\x05' + le32(switch_static_maps_va))                # eax = &map_entry[idx]
        a.raw(b'\x89\xC7')                                          # edi = entry
        a.raw(b'\x89\xEA')                                          # edx = active map name
        a.raw(b'\x89\xFB')                                          # ebx = entry name

        a.label('lsw_str_loop')
        a.raw(b'\x8A\x02')                                          # al = [active]
        a.raw(b'\x3A\x03')                                          # cmp al, [entry]
        a.jnz('lsw_next_map')
        a.raw(b'\x84\xC0'); a.jz('lsw_match')                       # both NUL -> equal
        a.raw(b'\x42\x43')                                          # inc edx; inc ebx
        a.jmp('lsw_str_loop')

        a.label('lsw_next_map')
        a.raw(b'\x46')                                              # ++idx
        a.jmp('lsw_map_loop')

        a.label('lsw_match')
        a.raw(b'\x89\xFD')                                          # ebp = map record (name done)
        a.raw(b'\x8B\x4D' + bytes([cfg.SWITCH_MAP_NAME_SLOT]))      # ecx = switch count
        a.raw(b'\x83\xF9' + bytes([cfg.SWITCH_TABLE_MAX]))          # cmp ecx, live cap
        a.jbe('lsw_count_ok')
        a.raw(b'\xB9' + le32(cfg.SWITCH_TABLE_MAX))                 # cap live count
        a.label('lsw_count_ok')
        a.raw(b'\x89\x0D' + le32(switch_count_va))                  # switch_count = ecx
        a.raw(b'\x85\xC9'); a.jz('lsw_done')
        a.raw(b'\x8B\x5D' + bytes([cfg.SWITCH_MAP_NAME_SLOT + 4]))  # ebx = first switch idx
        # Points: src = &static_points[first*8], n = count*2 dwords.
        a.raw(b'\x8D\x34\xDD' + le32(switch_static_points_va))      # lea esi, [ebx*8 + points]
        a.raw(b'\xBF' + le32(switch_table_va))                      # edi = live switch_table
        a.raw(b'\xD1\xE1')                                          # ecx *= 2 dwords per point
        a.raw(b'\xFC\xF3\xA5')                                      # cld; rep movsd
        # Flags: src = &static_flags[first], n = switch_count bytes.
        a.raw(b'\x8B\x0D' + le32(switch_count_va))                  # ecx = switch_count
        a.raw(b'\x8D\xB3' + le32(switch_static_flags_va))           # lea esi, [ebx + flags]
        a.raw(b'\xBF' + le32(switch_flags_va))                      # edi = live switch_flags
        a.raw(b'\xF3\xA4')                                          # rep movsb
        # Pairs: count/first from the second record pair. SWITCH_PAIR_MAX
        # (160) exceeds a sign-extended imm8 -> imm32 compare form.
        a.raw(b'\x8B\x4D' + bytes([cfg.SWITCH_MAP_NAME_SLOT + 8]))  # ecx = pair count
        a.raw(b'\x81\xF9' + le32(cfg.SWITCH_PAIR_MAX))              # cmp ecx, live cap
        a.jbe('lsw_pair_count_ok')
        a.raw(b'\xB9' + le32(cfg.SWITCH_PAIR_MAX))                  # cap live count
        a.label('lsw_pair_count_ok')
        a.raw(b'\x89\x0D' + le32(switch_pair_count_va))             # pair_count = ecx
        a.raw(b'\x85\xC9'); a.jz('lsw_done')
        a.raw(b'\x8B\x5D' + bytes([cfg.SWITCH_MAP_NAME_SLOT + 12])) # ebx = first pair idx
        a.raw(b'\x8D\x34\x9D' + le32(switch_static_pairs_va))       # lea esi, [ebx*4 + pairs]
        a.raw(b'\xBF' + le32(switch_pairs_va))                      # edi = live switch_pairs
        a.raw(b'\xF3\xA5')                                          # rep movsd (ecx = count dwords)

        if layout.has_field('switch_node') and layout.has_field('sww_spill'):
            # Bind each live switch to its nearest graph node (wp_load ran
            # earlier in detour_df90, so the graph is in). Sits after the
            # pairs copy, so pairless maps (relay/bin-only switches) skip it —
            # the wander-bump only targets door-opening switches, which by
            # definition carry pairs. The loop index must survive
            # wp_find_nearest — spill it in sww_spill.
            a.raw(b'\x83\x3D' + le32(layout.va('overlay_vertex_count')) + b'\x00')
            a.jz('lsw_done')                                        # no graph -> unbound
            a.raw(b'\xC7\x05' + le32(layout.va('sww_spill')) + le32(0))
            a.label('lsw_bind_loop')
            a.raw(b'\xA1' + le32(layout.va('sww_spill')))           # eax = s
            a.raw(b'\x3B\x05' + le32(switch_count_va))              # s >= switch_count?
            a.jae('lsw_done')
            a.raw(b'\x83\xF8' + bytes([cfg.SWITCH_TABLE_MAX]))      # s >= table max?
            a.jae('lsw_done')
            a.raw(b'\x8B\x0C\xC5' + le32(switch_table_va))          # ecx = switch.x
            a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch')))
            a.raw(b'\x8B\x0C\xC5' + le32(switch_table_va + 4))      # ecx = switch.y
            a.raw(b'\x89\x0D' + le32(layout.va('wp_scratch') + 4))
            a.call_lbl('wp_find_nearest')                           # ebx = nearest or -1
            a.raw(b'\xA1' + le32(layout.va('sww_spill')))           # eax = s
            a.raw(b'\x89\x1C\x85' + le32(layout.va('switch_node'))) # switch_node[s] = ebx
            a.raw(b'\xFF\x05' + le32(layout.va('sww_spill')))       # ++s
            a.jmp('lsw_bind_loop')

        a.label('lsw_done')
        a.raw(b'\x61')                                              # popad
        a.raw(b'\xC3')

    # =====================================================================
    # switch_blocked_census(ECX = switch idx) -> EAX = number of paired doors
    # currently blocked. Shared by switch_wander_check (candidate filter +
    # roll-time census) and the follower's bump-approach block (success = the
    # census CHANGED since latch — registers for openers AND togglers).
    # Clobbers edx/esi/ebx; preserves ecx. Bounded by the live pair count and
    # the static cap.
    #
    # switch_wander_check(ECX = current node idx) -> EAX = switch idx+1 to
    # bump, or 0. Called from the follower's roam fallback (inside its pushad
    # frame; may clobber GPRs). Mirrors portal_wander_check: the FIRST
    # door-opening switch bound to this node with >=1 paired door blocked
    # (toggle-safety: a toggler with all its doors open is never bumped shut)
    # rolls RNG(0..99) < switch_wander_chance; the census at roll time lands
    # in sww_census for the caller to snapshot into bot_switch_snap.
    # =====================================================================
    if not (
        layout.has_field('bot_switch_target')
        and layout.has_field('switch_node')
        and layout.has_field('door_blocked')
        and layout.has_field('sww_spill')
    ):
        a.label('switch_blocked_census')
        a.raw(b'\x31\xC0\xC3')                                      # xor eax,eax; ret
        a.label('switch_wander_check')
        a.raw(b'\x31\xC0\xC3')                                      # xor eax,eax; ret
    else:
        a.label('switch_blocked_census')
        a.raw(b'\x31\xC0')                                          # eax = 0 (census)
        a.raw(b'\x31\xD2')                                          # edx = 0 (p)
        a.label('sbc_loop')
        a.raw(b'\x3B\x15' + le32(layout.va('switch_pair_count')))   # p >= pair_count?
        a.jae('sbc_done')
        a.raw(b'\x81\xFA' + le32(cfg.SWITCH_PAIR_MAX))              # p >= pair cap?
        a.jae('sbc_done')
        a.raw(b'\x8B\x34\x95' + le32(layout.va('switch_pairs')))    # esi = pairs[p]
        a.raw(b'\x0F\xB7\xDE')                                      # ebx = low16 (switch idx)
        a.raw(b'\x39\xCB')                                          # cmp ebx, ecx
        a.jnz('sbc_next')
        a.raw(b'\xC1\xEE\x10')                                      # esi >>= 16 (door idx)
        a.raw(b'\x3B\x35' + le32(layout.va('door_count')))          # door idx valid?
        a.jae('sbc_next')
        a.raw(b'\x83\x3C\xB5' + le32(layout.va('door_blocked')) + b'\x00')
        a.jz('sbc_next')                                            # open -> not counted
        a.raw(b'\x40')                                              # ++census
        a.label('sbc_next')
        a.raw(b'\x42')                                              # ++p
        a.jmp('sbc_loop')
        a.label('sbc_done')
        a.raw(b'\xC3')

        a.label('switch_wander_check')
        a.raw(b'\x83\x3D' + le32(layout.va('switch_wander_chance')) + b'\x00')
        a.jz('swc_zero')
        a.raw(b'\x31\xFF')                                          # edi = 0 (s)
        a.label('swc_loop')
        a.raw(b'\x3B\x3D' + le32(layout.va('switch_count')))        # s >= switch_count?
        a.jae('swc_zero')
        a.raw(b'\x83\xFF' + bytes([cfg.SWITCH_TABLE_MAX]))          # s >= table max?
        a.jae('swc_zero')
        a.raw(b'\x39\x0C\xBD' + le32(layout.va('switch_node')))     # switch_node[s] == cur?
        a.jnz('swc_next')
        a.raw(b'\xF6\x87' + le32(layout.va('switch_flags'))
              + bytes([door_data.SWITCH_FLAG_OPENS_DOORS]))         # opener class?
        a.jz('swc_next')
        a.raw(b'\x51')                                              # push ecx (cur)
        a.raw(b'\x89\xF9')                                          # ecx = s
        a.call_lbl('switch_blocked_census')                         # eax = census
        a.raw(b'\x59')                                              # pop ecx (cur)
        a.raw(b'\x85\xC0')                                          # census == 0?
        a.jz('swc_next')                                            # nothing to open
        a.raw(b'\xA3' + le32(layout.va('sww_census')))              # stash roll-time census
        a.raw(b'\x89\x3D' + le32(layout.va('sww_spill')))           # spill s (RNG clobbers)
        a.raw(b'\x6A\x63')                                          # push 99 (high)
        a.raw(b'\x6A\x00')                                          # push 0  (low)
        a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                        # ecx = RNG instance
        a.call_va(ax.RNG_SUB)                                       # eax = 0..99 (callee pops)
        a.raw(b'\x3B\x05' + le32(layout.va('switch_wander_chance')))
        a.jae('swc_zero')                                           # roll failed -> no bump
        a.raw(b'\xA1' + le32(layout.va('sww_spill')))               # eax = s
        a.raw(b'\x40')                                              # eax = s+1
        a.raw(b'\xC3')
        a.label('swc_next')
        a.raw(b'\x47')                                              # ++s
        a.jmp('swc_loop')
        a.label('swc_zero')
        a.raw(b'\x31\xC0')                                          # eax = 0
        a.raw(b'\xC3')

