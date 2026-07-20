"""Switch detection (CollideTriggerAI) + switch-seek / wander-bump."""

# --- Switch detection (CollideTriggerAI bump switches) ---------------------
# Census over the shipped Data.dat (2026-07-18): 116 collide switches across
# 17 MP maps (15 with switches + 2 door-only), and ALL of them are
# `Triggered By Players=1` / `Projectiles=0` / repeatable / authored active —
# every MP switch is a walk-into-it bump switch a bot can fire by steering
# into it (no shooting needed). Classes (switch_static_flags bits, see
# door_data.SWITCH_FLAG_*): door open/togglers (Torture Chamber's 4 pillar
# togglers bind to all 43 pillar doors; Doom ship lights; Battle on the Ice
# team doors; Curse first/last/spike doors), trap switches (CCloseDoorAction
# jaws/spikes/"Player N door" lockouts), SK deposit bins (CUseCannedAction
# 'Bin NN'), and script relays. The static tables mirror the door pipeline:
# per-map switch centers + class bytes + (switch, door) open/toggle pairs
# (door indices reference the SAME map's door_table order), packed at build
# time and copied per match by load_switches. DETECTION layer only — overlay
# markers + tables; switch-seek routing consumes these later.
SWITCH_DETECT_ENABLED = True
# --- Switch-seek routing (consumes the detection tables) -------------------
# When a routed CTF bot's goal is OPEN-FIELD UNREACHABLE from its node (sealed
# Torture Chamber base) or reachable only via a detour SHORTCUT_GAIN+ hops
# longer than the full field's closed-door path (Battle on the Ice: a red bot
# inside the blue base with the team-gated blue door shut), it requests a
# switch seek. The page-flip eval (one candidate BFS per frame, cheap) picks
# the best viable candidate: OPENS_DOORS class, a currently-BLOCKED paired
# door (also the toggle-safety gate — never bump a toggler whose doors are
# open, it would CLOSE them), a bound graph node, and the smallest full-field
# distance to the requester's goal (selects the switch by the blocked
# frontier, not across the map); viable = the requester's node reaches the
# switch node in a team-door-gated BFS rooted at the switch (the Battle on
# the Ice reachability constraint: the blue-base switch is reachable for red
# only from inside). Activation is per team; participating bots descend the
# seek field and final-approach the switch center to BUMP it (all MP switches
# are player-bump). The opened door then flows through the normal
# door_dirty -> rebuild -> route-epoch machinery, which also CLEARS all seek
# state (fields are stale the moment doors change); bots re-request if still
# blocked. A seek that makes no door change for TIMEOUT frames blacklists
# that switch until the next door-state change.
SWITCH_SEEK_ENABLED = True
SWITCH_SEEK_TIMEOUT_FRAMES = 900   # ~15 s at 60 Hz before an active seek expires
# full+GAIN < open triggers the shortcut request. UNITS: since the weighted-
# routing change, every BFS distance is PHYSICAL length in WP_EDGE_LEN_QUANTUM
# (16 px) units, not hops — so GAIN=20 means "the through-door route must be
# at least ~320 px shorter than the current open route". The motivating case
# (Hydroplant Bouncefest, live-reported): base-to-base is 9 hops BOTH through
# the doors and around the top, so the old hop metric saw zero benefit and
# the seek never fired — but physically the door route is 1899 px vs 2580 px
# around (42 quanta gap), which clears this threshold comfortably. The
# request is cheap and activation carries its own BENEFIT check (walk +
# post-open route must beat the current open route), so a low trigger
# threshold cannot cause silly cross-map detours. Must stay <= 127 (imm8).
SWITCH_SEEK_SHORTCUT_GAIN  = 20
# Per-bot ON-THE-WAY join gate for an ACTIVE team seek. A bot arriving at a
# node joins the descent only when the switch detour is local:
#   seek_dist[cur] + full_dist(switch_node -> goal)
#     <= full_dist(cur -> goal) + JOIN_SLACK
# (a bot whose full-field goal distance is itself unreachable joins
# unconditionally — nothing better exists). Live-diagnosed on Battle on the
# Ice (2026-07-20 R snapshots): the join used to be unconditional for every
# team bot that could reach the switch, so whenever the self-closing south
# team door re-closed and a trailing bot re-requested its adjacent switch,
# teammates PAST the door — one at node 13/14, half the map away — turned
# around and walked backwards toward it (snap6: bot_seek=[1,1,1,1,1]; slot 1
# visibly backtracked 14->54 and flipped forward again on the next rebuild).
# UNITS: WP_EDGE_LEN_QUANTUM (16 px) quanta, same as every BFS field; 24 =
# ~384 px of acceptable local detour. Sized from the live map's gap: the
# south-side node 47 detours 16 quanta (joins — it must, the switch is its
# way through) while node 48, already NORTH of the doorway, detours 38
# (must NOT join — descending back through the door was the reported
# backwards-forwards shuttle). Must stay <= 127 (imm8).
SWITCH_SEEK_JOIN_SLACK     = 24
# Roam-time switch WANDER-BUMP (the "switches are part of the random path"
# layer). The seek machinery above only serves ROUTED bots — a goal-less bot
# (DM roam, CTF missing-flag search, suspension roam) never requests one, and
# some maps structurally never trip the seek gate at all (Hydroplant
# Bouncefest: base-to-base is equal-cost around every door, live-diagnosed
# 2026-07-19 with both flags away pinning both bots in permanent roam). A
# ROAMING bot that ARRIVES at a node hosting a door-opening switch with >=1
# paired door currently blocked (the same toggle-safety gate as seek
# candidates: a toggler with its doors open is never bumped shut) now rolls
# RNG(0..99) < SWITCH_WANDER_CHANCE and, on success, final-approaches the
# switch CENTER to physically BUMP it — through the standard watchdog +
# press-patience machinery (mirror of the pad/door patience). Success is a
# CHANGE in the switch's blocked-paired-door census (works for openers AND
# togglers); success or exhausted patience arms the per-bot cooldown so the
# bot cannot orbit one switch. Deliberately NOT gated on routing suspension:
# unlike the portal wander roll (which can fling a suspended bot across the
# map), a bump is local and can open the exact door the bot is wedged at.
SWITCH_WANDER_ENABLED = True
SWITCH_WANDER_CHANCE = 35            # percent per roam arrival at a blocked switch's node
SWITCH_WANDER_COOLDOWN_FRAMES = 900  # ~15 s between bump attempts per bot
SWITCH_WANDER_PRESS_PATIENCE = 2     # fresh watchdog cycles pressing the switch
# Door-press patience. Live trace (Battle on the Ice, 2026-07-18): a red bot
# wedged at its own closed walk-up door entered the door's tiny trigger oval
# via the wall-slide sweep after ~2 s — the SAME timescale as the routed
# progress timeout, whose suspension threw the bot into a roam at the exact
# moment the door opened (blk flipped 0 for one sample while susp=234). While
# the timeout fires WEDGED AGAINST A CLOSED DOOR (door_capture_wedge latch),
# the follower now resets the watchdog and keeps pressing (the slide sweep
# re-runs, each cycle another chance to catch the oval) for up to this many
# timeout cycles before the normal suspension takes over. Truly-impassable
# doors (sealed pillars) just delay their roam by ~2 cycles.
WP_DOOR_PRESS_PATIENCE = 3
SWITCH_TABLE_MAX        = 20    # live per-map switches (Foundry peaks at 19)
SWITCH_PAIR_MAX         = 160   # live per-map (switch, door) pairs (Curse: 158)
SWITCH_STATIC_MAP_MAX   = 20    # shipped Data.dat has 17 MP switch/door maps
SWITCH_STATIC_POINT_MAX = 128   # shipped Data.dat has 116 MP switches
SWITCH_STATIC_PAIR_MAX  = 288   # shipped Data.dat has 255 pairs
SWITCH_MAP_NAME_SLOT    = 96    # fixed ASCII bytes per map path, incl. NUL

