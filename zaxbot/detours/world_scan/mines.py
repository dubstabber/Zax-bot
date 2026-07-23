"""``load_mine`` + ``mine_tick`` — proximity-mine placement support.

``load_mine`` (df90, per match): one rep-stosd clear of the live mine block
(ring, cursor, per-bot cooldowns, diag counters, cached keys), then the two
key resolves the engine itself performs: the "Proximity Mine" item-def key
(``sub_523DF0`` on the item-def registry — the same id space ``[item+8]``
carries and ``sub_426860`` counts) and the "Secondary" inventory-group key
(``sub_523DF0`` on the slot-name registry — the exact call the engine's
secondary-fire input path makes at 0x544876).

``mine_tick`` (page flip, once per frame):
1. TTL pass — ages every live ring slot; the deployed mine's own MP script
   warp-deletes it after ~15 s, so an expired TTL simply mirrors the
   engine's removal (no liveness scan needed).
2. Placement pass — for each live bot: tick its cooldown; when it hits 0,
   re-arm the short retry window and attempt a placement: carried-rounds
   gate (``sub_426860``), RNG roll against the scratch ``mine_place_chance``
   knob, a spacing check against every live ring mine (no stacking a wedged
   bot's whole reserve on one spot), then force-select the mine item in the
   bot's Secondary slot (engine group iterate ``sub_425350``/``sub_424F60``
   by def key + the spawn.py select/force-switch sequence) and fire the
   engine's own deploy ``sub_5AB9B0(char)`` — THROUGH the patched entry, so
   the ``detour_5AB9B0`` registration detour records the mine exactly like
   a host-human placement. Success is the carried-round DELTA (the deploy
   returns 0 on both paths); success re-arms the long cooldown.

CTF-specific placement rules are a planned follow-up: they gate the
placement pass per bot (e.g. only near the own base approach) before the
RNG roll.
"""

from ... import addresses as ax
from ... import config as cfg
from ...asm import Asm, le32
from ...layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    mine_on = (
        layout.has_field('mine_def_key')
        and layout.has_field('mine_ttl')
        and layout.has_field('mine_pos')
        and layout.has_field('bot_mine_cd')
    )
    if not mine_on:
        a.label('load_mine')
        a.raw(b'\xC3')
        a.label('mine_tick')
        a.raw(b'\xC3')
        return

    def_key_va   = layout.va('mine_def_key')
    sec_key_va   = layout.va('mine_sec_key')
    ttl_va       = layout.va('mine_ttl')
    pos_va       = layout.va('mine_pos')
    cd_va        = layout.va('bot_mine_cd')
    chance_va    = layout.va('mine_place_chance')
    spacing_va   = layout.va('mine_spacing_sq')
    place_cnt_va = layout.va('mine_place_count')
    tmp_slot_va  = layout.va('mine_tmp_slot')
    tmp_char_va  = layout.va('mine_tmp_char')
    tmp_cnt_va   = layout.va('mine_tmp_cnt')
    tmp_id_va    = layout.va('mine_tmp_id')
    spill_va     = layout.va('mine_spill')
    clear_dwords = (layout.field('mine_pos').end
                    - layout.field('mine_def_key').offset) // 4

    # =====================================================================
    # load_mine: per-match clear + key resolves. pushad/popad, no args.
    # =====================================================================
    a.label('load_mine')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\xFC')                                              # cld
    a.raw(b'\xBF' + le32(def_key_va))                           # edi = block base
    a.raw(b'\xB9' + le32(clear_dwords))                         # ecx = dwords
    a.raw(b'\x31\xC0')                                          # eax = 0
    a.raw(b'\xF3\xAB')                                          # rep stosd
    # "Proximity Mine" item-def key (registry populated at startup, long
    # before any match change — same timing as load_sk's resolves).
    a.raw(b'\x6A\xFF')                                          # push -1
    a.raw(b'\x68' + le32(ax.PROXIMITY_MINE_STR_VA))             # push "Proximity Mine"
    a.raw(b'\xB9' + le32(ax.ITEM_DEF_REGISTRY_VA))              # ecx = item-def registry
    a.call_va(ax.SUB_523DF0_VA)                                 # eax = key (ret 8)
    a.raw(b'\xA3' + le32(def_key_va))
    # "Secondary" inventory-group key (the engine's own call at 0x544876).
    a.raw(b'\x6A\xFF')                                          # push -1
    a.raw(b'\x68' + le32(ax.SECONDARY_STR_VA))                  # push "Secondary"
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))             # ecx = slot-name registry
    a.call_va(ax.SUB_523DF0_VA)                                 # eax = group key
    a.raw(b'\xA3' + le32(sec_key_va))
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')

    # =====================================================================
    # mine_tick: TTL pass + per-bot placement pass. pushad/popad, no args.
    # =====================================================================
    a.label('mine_tick')
    a.raw(b'\x60')                                              # pushad
    a.raw(b'\x83\x3D' + le32(def_key_va) + b'\x00')             # keys resolved?
    a.jz('mt_out')

    # --- TTL pass: age every live ring slot.
    a.raw(b'\x31\xC9')                                          # ecx = 0
    a.label('mt_ttl')
    a.raw(b'\x83\xF9' + bytes([cfg.MINE_TABLE_MAX]))            # slot >= ring size?
    a.jae('mt_place')
    a.raw(b'\x8B\x04\x8D' + le32(ttl_va))                       # eax = ttl[slot]
    a.raw(b'\x85\xC0'); a.jz('mt_ttl_next')
    a.raw(b'\x48')                                              # --ttl
    a.raw(b'\x89\x04\x8D' + le32(ttl_va))
    a.label('mt_ttl_next')
    a.raw(b'\x41')                                              # ++slot
    a.jmp('mt_ttl')

    # --- Placement pass.
    a.label('mt_place')
    a.raw(b'\x31\xF6')                                          # esi = bot slot
    a.label('mt_loop')
    a.raw(b'\x89\x35' + le32(tmp_slot_va))                      # spill slot (engine
                                                                # calls below; mt_next
                                                                # reloads from here)
    # Manager / char array re-fetched per iteration (ebx/ebp don't need to
    # survive the call-heavy attempt path this way).
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))                 # eax = [mgr]
    a.raw(b'\x85\xC0'); a.jz('mt_out')
    a.raw(b'\x8B\x98\x94\x02\x00\x00')                          # ebx = char count
    a.raw(b'\x8B\x80\x90\x02\x00\x00')                          # eax = char array
    a.raw(b'\x85\xC0'); a.jz('mt_out')
    a.raw(b'\x8B\x14\xB5' + le32(layout.va('bot_indices')))     # edx = bot_indices[slot]
    a.raw(b'\x85\xD2'); a.jz('mt_next')                         # idx==0 -> host/unused
    a.raw(b'\x39\xDA'); a.jae('mt_next')                        # idx >= count
    a.raw(b'\x8B\x3C\x90')                                      # edi = char ptr
    a.raw(b'\x85\xFF'); a.jz('mt_next')
    # Cooldown countdown.
    a.raw(b'\x8B\x04\xB5' + le32(cd_va))                        # eax = cd[slot]
    a.raw(b'\x85\xC0'); a.jz('mt_try')
    a.raw(b'\x48')                                              # --cd
    a.raw(b'\x89\x04\xB5' + le32(cd_va))
    a.jmp('mt_next')

    a.label('mt_try')
    # Re-arm the retry window FIRST — every failure path below just falls
    # out and the bot re-rolls in MINE_PLACE_RETRY_FRAMES.
    a.raw(b'\xC7\x04\xB5' + le32(cd_va)
          + le32(max(1, cfg.MINE_PLACE_RETRY_FRAMES)))
    a.raw(b'\x89\x3D' + le32(tmp_char_va))                      # mine_tmp_char = char

    # --- CTF TERRITORY GATE (user rule: mine the ENEMY half, rarely the
    # middle, never the own half). Territory = PATH distance to each base
    # from the CTF routing BFS fields (flag_dist, WP_EDGE_LEN_QUANTUM
    # units — walls count, unlike a Euclidean split): at the bot's current
    # graph node, enemy_d + band < own_d ⇒ enemy half (place); own_d +
    # band < enemy_d ⇒ own half (deny); the |diff| <= band strip is the
    # middle ⇒ extra RNG roll < mine_ctf_mid_chance. Inert outside CTF
    # (menu_mode gate) and while routing is unarmed; missing/unreachable
    # distances fall back to place-anywhere. Runs BEFORE the rounds/RNG
    # work; mine_tmp_id/mine_tmp_cnt/mine_spill are free as temps here
    # (their real uses start below).
    territory_on = (
        cfg.MINE_CTF_TERRITORY_ENABLED
        and layout.has_field('flag_dist')
        and layout.has_field('flag_team')
        and layout.has_field('flag_routing_active')
        and layout.has_field('menu_mode')
        and layout.has_field('bot_team')
        and layout.has_field('bot_current_wp')
        and layout.has_field('wp_scratch')
        and layout.has_field('mine_ctf_mid_band')
    )
    if territory_on:
        vmax = cfg.OVERLAY_VERTEX_MAX
        vshift = vmax.bit_length() - 1
        assert (1 << vshift) == vmax, 'flag_dist row indexing needs power-of-two VMAX'
        a.raw(b'\x83\x3D' + le32(layout.va('menu_mode')) + b'\x01')  # CTF?
        a.jnz('mtg_pass')
        a.raw(b'\x83\x3D' + le32(layout.va('flag_routing_active')) + b'\x00')
        a.jz('mtg_pass')
        a.raw(b'\x83\x3D' + le32(layout.va('flag_count')) + b'\x02')
        a.jb('mtg_pass')                                        # need both bases
        # Node: the follower's current target node, else nearest to the bot.
        a.raw(b'\x8B\x35' + le32(tmp_slot_va))                  # esi = slot
        a.raw(b'\x8B\x04\xB5' + le32(layout.va('bot_current_wp')))
        a.raw(b'\x83\xF8\xFF'); a.jnz('mtg_node_ok')
        a.raw(b'\x8B\x0D' + le32(tmp_char_va))                  # ecx = char
        a.raw(b'\x8B\x51' + bytes([ax.ENTITY_POS_X_OFF]))       # edx = x bits
        a.raw(b'\x89\x15' + le32(layout.va('wp_scratch')))
        a.raw(b'\x8B\x51' + bytes([ax.ENTITY_POS_Y_OFF]))       # edx = y bits
        a.raw(b'\x89\x15' + le32(layout.va('wp_scratch') + 4))
        a.call_lbl('wp_find_nearest')                           # ebx = node / -1
        a.raw(b'\x8B\xC3')                                      # eax = node
        a.raw(b'\x83\xF8\xFF'); a.jz('mt_next')                 # no node -> hold fire
        a.label('mtg_node_ok')
        a.raw(b'\x3D' + le32(vmax)); a.jae('mt_next')           # defensive row bound
        a.raw(b'\xA3' + le32(tmp_id_va))                        # node
        # My team; then scan the bases for own/enemy path distance.
        a.raw(b'\xA1' + le32(tmp_slot_va))                      # eax = slot
        a.raw(b'\x8B\x3C\x85' + le32(layout.va('bot_team')))    # edi = my team
        a.raw(b'\xC7\x05' + le32(spill_va) + le32(0xFFFFFFFF))  # own_d = -1
        a.raw(b'\xC7\x05' + le32(tmp_cnt_va) + le32(0xFFFFFFFF))  # enemy_d = -1
        a.raw(b'\x31\xF6')                                      # esi = base idx
        a.label('mtg_base_loop')
        a.raw(b'\x3B\x35' + le32(layout.va('flag_count')))
        a.jae('mtg_eval')
        a.raw(b'\x83\xFE' + bytes([cfg.FLAG_ROUTE_MAX]))        # defensive cap
        a.jae('mtg_eval')
        a.raw(b'\x8B\xCE')                                      # ecx = base
        a.raw(b'\xC1\xE1' + bytes([vshift]))                    # ecx = base*VMAX
        a.raw(b'\x03\x0D' + le32(tmp_id_va))                    # + node
        a.raw(b'\x8B\x14\x8D' + le32(layout.va('flag_dist')))   # edx = dist
        a.raw(b'\x8B\x04\xB5' + le32(layout.va('flag_team')))   # eax = base team
        a.raw(b'\x3B\xC7')                                      # mine or theirs?
        a.jnz('mtg_enemyd')
        a.raw(b'\x89\x15' + le32(spill_va))                     # own_d = dist
        a.jmp('mtg_nextb')
        a.label('mtg_enemyd')
        a.raw(b'\x89\x15' + le32(tmp_cnt_va))                   # enemy_d = dist
        a.label('mtg_nextb')
        a.raw(b'\x46')                                          # ++base
        a.jmp('mtg_base_loop')
        a.label('mtg_eval')
        a.raw(b'\xA1' + le32(spill_va))                         # eax = own_d
        a.raw(b'\x83\xF8\xFF'); a.jz('mtg_pass')                # unknown -> allow
        a.raw(b'\x8B\x15' + le32(tmp_cnt_va))                   # edx = enemy_d
        a.raw(b'\x83\xFA\xFF'); a.jz('mtg_pass')
        # Enemy half: enemy_d + band < own_d -> place.
        a.raw(b'\x8B\xCA')                                      # ecx = enemy_d
        a.raw(b'\x03\x0D' + le32(layout.va('mine_ctf_mid_band')))
        a.raw(b'\x3B\xC8')                                      # cmp ecx, own_d
        a.jb('mtg_pass')
        # Own half: own_d + band < enemy_d -> deny.
        a.raw(b'\x8B\xC8')                                      # ecx = own_d
        a.raw(b'\x03\x0D' + le32(layout.va('mine_ctf_mid_band')))
        a.raw(b'\x3B\xCA')                                      # cmp ecx, enemy_d
        a.jb('mt_next')
        # Middle strip: rare extra roll.
        a.raw(b'\x6A\x63')                                      # push 99
        a.raw(b'\x6A\x00')                                      # push 0
        a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                    # ecx = RNG
        a.call_va(ax.RNG_SUB)                                   # eax = 0..99
        a.raw(b'\x3B\x05' + le32(layout.va('mine_ctf_mid_chance')))
        a.jae('mt_next')
        a.label('mtg_pass')

    # Carried mine rounds (the engine's own counter; 0 = nothing to place).
    a.raw(b'\x8B\x15' + le32(def_key_va))                       # edx = mine def key
    a.raw(b'\x8B\x0D' + le32(tmp_char_va))                      # ecx = char (edi is
                                                                # gate-clobbered)
    a.call_va(ax.SUB_426860_VA)                                 # eax = rounds
    a.raw(b'\x85\xC0'); a.jz('mt_next')
    a.raw(b'\xA3' + le32(tmp_cnt_va))                           # pre-fire count
    # Placement roll: RNG(0..99) < mine_place_chance (scratch knob).
    a.raw(b'\x6A\x63')                                          # push 99
    a.raw(b'\x6A\x00')                                          # push 0
    a.raw(b'\xB9' + le32(ax.RNG_OBJ_VA))                        # ecx = RNG instance
    a.call_va(ax.RNG_SUB)                                       # eax = 0..99
    a.raw(b'\x3B\x05' + le32(chance_va))                        # cmp eax, chance
    a.jae('mt_next')
    # Spacing: no live ring mine within sqrt(mine_spacing_sq) of the bot.
    # With MINE_AVOID_OWN_ONLY the sweep considers only this bot's OWN
    # mines — its purpose is "don't stack YOUR reserve on one spot", and
    # an enemy-mined chokepoint must not block mining it too.
    a.raw(b'\x31\xC9')                                          # ecx = ring slot
    a.label('mt_sp')
    a.raw(b'\x83\xF9' + bytes([cfg.MINE_TABLE_MAX]))
    a.jae('mt_select')
    a.raw(b'\x83\x3C\x8D' + le32(ttl_va) + b'\x00')             # dead slot?
    a.jz('mt_sp_next')
    if cfg.MINE_AVOID_OWN_ONLY:
        a.raw(b'\x8B\x04\x8D' + le32(layout.va('mine_owner')))  # eax = owner
        a.raw(b'\x3B\x05' + le32(tmp_slot_va))                  # == my slot?
        a.jnz('mt_sp_next')
    a.raw(b'\x8B\x15' + le32(tmp_char_va))                      # edx = char
    a.raw(b'\xD9\x04\xCD' + le32(pos_va))                       # fld mine.x
    a.raw(b'\xD8\x62' + bytes([ax.ENTITY_POS_X_OFF]))           # fsub [char+0x4C]
    a.raw(b'\xD8\xC8')                                          # fmul st,st
    a.raw(b'\xD9\x04\xCD' + le32(pos_va + 4))                   # fld mine.y
    a.raw(b'\xD8\x62' + bytes([ax.ENTITY_POS_Y_OFF]))           # fsub [char+0x50]
    a.raw(b'\xD8\xC8')                                          # fmul st,st
    a.raw(b'\xDE\xC1')                                          # faddp -> d²
    a.raw(b'\xD8\x1D' + le32(spacing_va))                       # fcomp spacing² (pops)
    a.raw(b'\xDF\xE0'); a.raw(b'\x9E')                          # fnstsw ax; sahf
    a.jb('mt_next')                                             # too close -> skip window
    a.label('mt_sp_next')
    a.raw(b'\x41')                                              # ++ring slot
    a.jmp('mt_sp')

    # --- Ensure the mine is the SELECTED Secondary item, then deploy.
    a.label('mt_select')
    a.raw(b'\x8B\x0D' + le32(tmp_char_va))                      # ecx = char
    a.call_va(ax.SUB_4267E0_VA)                                 # eax = inventory
    a.raw(b'\x85\xC0'); a.jz('mt_next')
    a.raw(b'\xA3' + le32(spill_va))                             # mine_spill = inv
    # Fast path: the currently-selected Secondary item already IS the mine.
    a.raw(b'\xFF\x35' + le32(sec_key_va))                       # push group key
    a.raw(b'\x8B\xC8')                                          # ecx = inv
    a.call_va(ax.SUB_425290_VA)                                 # eax = sel id / -1
    a.raw(b'\x83\xF8\xFF'); a.jz('mt_find')
    a.raw(b'\x50')                                              # push sel id
    a.raw(b'\x8B\x0D' + le32(spill_va))                         # ecx = inv
    a.raw(b'\x8B\x11')                                          # edx = [inv] vtbl
    a.raw(b'\xFF\x52' + bytes([ax.INVENTORY_GET_WEAPON_OFF]))   # call [edx+0x68] -> item
    a.raw(b'\x85\xC0'); a.jz('mt_find')
    a.raw(b'\x8B\x50' + bytes([ax.ITEM_DEF_KEY_OFF]))           # edx = [item+8] def key
    a.raw(b'\x3B\x15' + le32(def_key_va))                       # == mine def?
    a.jz('mt_fire')

    # Slow path: iterate the Secondary group for the mine item by def key
    # (the engine's own auto-cycle shape at 0x544932..). TERMINATION:
    # sub_425350 WRAPS past the group end (see addresses.py), so the walk
    # carries the engine's sub_425470 last-item guard in EDI — the
    # carried-rounds gate above means a mine item EXISTS and the def
    # match normally exits first, but a naive loop here would spin
    # forever the day that invariant slips.
    a.label('mt_find')
    a.raw(b'\xFF\x35' + le32(sec_key_va))                       # push group key
    a.raw(b'\x6A\xFF')                                          # push -1
    a.raw(b'\x8B\x0D' + le32(spill_va))                         # ecx = inv
    a.call_va(ax.SUB_425470_VA)                                 # eax = last id / -1
    a.raw(b'\x8B\xF8')                                          # edi = last id
    a.raw(b'\x83\xC8\xFF')                                      # eax = -1 (prev)
    a.label('mt_find_loop')
    a.raw(b'\xFF\x35' + le32(sec_key_va))                       # push group key
    a.raw(b'\x50')                                              # push prev id
    a.raw(b'\x8B\x0D' + le32(spill_va))                         # ecx = inv
    a.call_va(ax.SUB_425350_VA)                                 # eax = next id / -1
    a.raw(b'\x83\xF8\xFF'); a.jz('mt_next')                     # empty group
    a.raw(b'\xA3' + le32(tmp_id_va))                            # mine_tmp_id = id
    a.raw(b'\x50')                                              # push id
    a.raw(b'\x8B\x0D' + le32(spill_va))                         # ecx = inv
    a.call_va(ax.SUB_424F60_VA)                                 # eax = item
    a.raw(b'\x85\xC0'); a.jz('mt_next')
    a.raw(b'\x8B\x50' + bytes([ax.ITEM_DEF_KEY_OFF]))           # edx = item def key
    a.raw(b'\x3B\x15' + le32(def_key_va))                       # == mine def?
    a.jz('mt_found')
    a.raw(b'\xA1' + le32(tmp_id_va))                            # eax = id
    a.raw(b'\x3B\xC7')                                          # id == last item?
    a.jz('mt_next')                                             # group end, no mine
    a.jmp('mt_find_loop')
    a.label('mt_found')
    # Found: select it. Clear the pending slot field first (sub_425590 bails
    # on a pending switch), call the engine selector, then force the switch
    # NOW by writing the slot fields — the exact spawn.py force-weapon
    # sequence, on the Secondary slot (stride 24: timer +0xC, current +0x10,
    # pending +0x14).
    a.raw(b'\x8B\x0D' + le32(spill_va))                         # ecx = inv
    a.raw(b'\x8B\x51\x10')                                      # edx = [inv+0x10] slots
    a.raw(b'\xA1' + le32(sec_key_va))                           # eax = group key
    a.raw(b'\x8D\x04\x40')                                      # eax *= 3
    a.raw(b'\xC1\xE0\x03')                                      # eax *= 8  (24 stride)
    a.raw(b'\xC7\x44\x02\x14\xFF\xFF\xFF\xFF')                  # pending = -1
    a.raw(b'\x6A\x01')                                          # push 1 (auto-equip)
    a.raw(b'\xFF\x35' + le32(tmp_char_va))                      # push char
    a.raw(b'\xFF\x35' + le32(sec_key_va))                       # push group key
    a.raw(b'\xFF\x35' + le32(tmp_id_va))                        # push item id
    a.raw(b'\x8B\x0D' + le32(spill_va))                         # ecx = inv
    a.call_va(ax.SUB_425590_VA)
    a.raw(b'\x8B\x0D' + le32(spill_va))                         # ecx = inv
    a.raw(b'\x8B\x51\x10')                                      # edx = slot array
    a.raw(b'\xA1' + le32(sec_key_va))                           # eax = group key
    a.raw(b'\x8D\x04\x40')                                      # eax *= 3
    a.raw(b'\xC1\xE0\x03')                                      # eax *= 8
    a.raw(b'\x8B\x0D' + le32(tmp_id_va))                        # ecx = item id
    a.raw(b'\x89\x4C\x02\x10')                                  # current = id
    a.raw(b'\xC7\x44\x02\x14\xFF\xFF\xFF\xFF')                  # pending = -1
    a.raw(b'\xC7\x44\x02\x0C\x00\x00\x00\x00')                  # switch timer = 0
    # fall through to fire

    a.label('mt_fire')
    # The engine's own deploy — called THROUGH the patched entry, so the
    # detour_5AB9B0 registration detour records the placement. Ownership
    # HANDSHAKE: announce the placing slot (as slot+1; 0 = idle) for the
    # detour to consume — matching the deploying char against a cached
    # pointer table was live-refuted 2026-07-23 (bot_chars[] is captured
    # at spawn and goes stale on the first respawn, so every post-respawn
    # placement mis-attributed to the human and the own-mine veto never
    # matched). The call is synchronous on the single main thread, so the
    # set/reset window cannot interleave with a human deploy.
    a.raw(b'\xA1' + le32(tmp_slot_va))                          # eax = slot
    a.raw(b'\x40')                                              # eax = slot+1
    a.raw(b'\xA3' + le32(layout.va('mine_placing_slot')))       # announce owner
    a.raw(b'\xFF\x35' + le32(tmp_char_va))                      # push char
    a.call_va(ax.SUB_5AB9B0_VA)                                 # __stdcall ret 4
    a.raw(b'\xC7\x05' + le32(layout.va('mine_placing_slot'))
          + le32(0))                                            # handshake idle
    # Success = the carried-round count dropped (the deploy has no useful
    # return value). On success re-arm the LONG cooldown.
    a.raw(b'\x8B\x15' + le32(def_key_va))                       # edx = mine def key
    a.raw(b'\x8B\x0D' + le32(tmp_char_va))                      # ecx = char
    a.call_va(ax.SUB_426860_VA)                                 # eax = rounds now
    a.raw(b'\x3B\x05' + le32(tmp_cnt_va))                       # vs pre-fire count
    a.jae('mt_next')                                            # unchanged -> failed
    a.raw(b'\x8B\x35' + le32(tmp_slot_va))                      # esi = slot
    a.raw(b'\xC7\x04\xB5' + le32(cd_va)
          + le32(max(1, cfg.MINE_PLACE_COOLDOWN_FRAMES)))
    a.raw(b'\xFF\x05' + le32(place_cnt_va))                     # ++mine_place_count

    a.label('mt_next')
    a.raw(b'\x8B\x35' + le32(tmp_slot_va))                      # esi = slot (calls
                                                                # above clobber-safe)
    a.raw(b'\x46')                                              # ++slot
    a.raw(b'\x83\xFE' + bytes([cfg.MAX_BOT_SLOTS]))
    a.jb('mt_loop')
    a.label('mt_out')
    a.raw(b'\x61')                                              # popad
    a.raw(b'\xC3')
