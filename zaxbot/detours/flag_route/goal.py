"""``ctf_pick_goal`` — per-frame goal selection (enemy base vs home
base by carry state) + the missing-flag search/wait policy roll."""

from ... import addresses as ax
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout, c) -> None:
    routing_active_va = c.routing_active_va
    route_carry_va = c.route_carry_va
    route_goal_va = c.route_goal_va
    flag_team_va = c.flag_team_va
    flag_count_va = c.flag_count_va
    flag_present_va = c.flag_present_va
    missing_policy_va = c.missing_policy_va
    missing_goal_va = c.missing_goal_va
    route_suspend_va = c.route_suspend_va
    bot_slot_va = c.bot_slot_va
    bot_char_va = c.bot_char_va
    bot_team_va = c.bot_team_va
    RMAX = c.RMAX
    ROW = c.ROW
    defender = c.defender
    standoff = c.standoff
    bot_role_va = c.bot_role_va
    defend_radius_va = c.defend_radius_va
    bot_current_wp_va = c.bot_current_wp_va
    flag_dist_va = c.flag_dist_va
    vcount_va = c.vcount_va

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
    if defender:
        # --- DEFENDER role (not carrying): hold near OWN base instead of
        # raiding. While the bot's current node is within defend_radius[home]
        # of the home base (full flag_dist field, quanta units) report NO
        # goal — the follower's random wp_advance roams it around the base;
        # beyond the radius the goal flips to the HOME base so normal
        # routing (door-aware, portals, seek) walks it back inside. The
        # per-frame re-evaluation makes this a self-correcting tether: a
        # roam step that leaves the zone is pulled back at the next arrival.
        # Deliberately BYPASSES the flag_present/missing-policy machinery —
        # a defender heads to its base area regardless of flag state (its
        # own stolen flag is exactly when guarding home matters), and since
        # the goal node itself is always inside the radius (dist 0), the
        # final approach can never steer a non-carrier into the flag.
        # Carrying defenders take the untouched carrier path above.
        a.raw(b'\x8B\x0D' + le32(bot_slot_va))                # ecx = slot
        a.raw(b'\xF6\x04\x8D' + le32(bot_role_va) + b'\x01')  # defender? (bit0;
        a.jz('cpg_role_atk')                                  # bit1 = route lane)
        a.raw(b'\x89\xF8')                                    # eax = home (edi)
        a.raw(b'\x83\xF8\xFF')                                # no home base?
        a.jz('cpg_store_goal')                                # -> store -1
        a.call_lbl('cpg_tether')                              # eax = home or -1
        a.jmp('cpg_store_goal')
        a.label('cpg_role_atk')
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
    # capture entities that would otherwise stay camera-gated. missing_goal is
    # still written either way — the dropped-flag pursuit keys its any-distance
    # latch off it, so a carrier whose home flag lies DROPPED somewhere routes
    # to it and returns it (unlocking its own capture).
    # STANDOFF (cfg.CTF_CARRIER_STANDOFF_ENABLED): a carrier in this state
    # cannot capture until its flag returns, so whole-map search roaming just
    # walks it away from where the capture will happen (user-reported). Apply
    # the defender tether instead: goal = HOME while beyond defend_radius
    # (route back), no goal inside it (near-base roam) — the bot hovers at its
    # base, ready. The inside-radius flip still guarantees no final approach
    # into the empty base (goal-node dist is 0), and the home force-tick stays
    # off through its flag_present[home] gate. Without the flag: the old
    # unconditional whole-map search.
    a.raw(b'\x83\x3D' + le32(route_carry_va) + b'\x00')        # carrying?
    a.jz('cpg_missing_attacker')
    a.raw(b'\x8B\x0D' + le32(bot_slot_va))                     # ecx = slot
    a.raw(b'\xC7\x04\x8D' + le32(missing_policy_va) + le32(1)) # policy = search
    a.raw(b'\x89\x04\x8D' + le32(missing_goal_va))             # missing_goal[slot] = goal
    if standoff:
        a.call_lbl('cpg_tether')                               # eax = home or -1
    else:
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                         # no goal -> random graph roam
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

    if defender:
        # =================================================================
        # cpg_tether(EAX = home base idx, valid) -> EAX = home (route back)
        # or -1 (roam here). The shared near-base tether: keeps the goal
        # only while the bot's current node lies BEYOND defend_radius[home]
        # in the full flag_dist field; inside the zone (or with no usable
        # node/unreachable base) it reports no goal so the follower's
        # random wp_advance roams locally. Used by the DEFENDER role and
        # the carrier STANDOFF. Clobbers ecx/edx/esi.
        # =================================================================
        a.label('cpg_tether')
        a.raw(b'\x8B\x0D' + le32(bot_slot_va))                # ecx = slot
        a.raw(b'\x8B\x14\x8D' + le32(bot_current_wp_va))      # edx = current node
        a.raw(b'\x3B\x15' + le32(vcount_va))                  # unlatched (-1) / bad?
        a.jae('cpg_tether_roam')
        a.raw(b'\x69\xF0' + le32(ROW))                        # imul esi, eax, ROW
        a.raw(b'\x8D\x34\x96')                                # lea esi, [esi + edx*4]
        a.raw(b'\x8B\xB6' + le32(flag_dist_va))               # esi = flag_dist[home][node]
        a.raw(b'\x83\xFE\xFF')                                # unreachable?
        a.jz('cpg_tether_roam')
        a.raw(b'\x3B\x34\x85' + le32(defend_radius_va))       # dist vs defend_radius[home]
        a.ja('cpg_tether_ret')                                # beyond -> keep eax = home
        a.label('cpg_tether_roam')
        a.raw(b'\xB8\xFF\xFF\xFF\xFF')                        # inside/unknown -> roam
        a.label('cpg_tether_ret')
        a.raw(b'\xC3')

