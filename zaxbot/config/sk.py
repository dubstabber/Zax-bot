"""Salvage King awareness, death piles and the generalized goody pursuit."""

# --- Salvage King (SK / "Greed") bot awareness ----------------------------
# The SK loop: collect ore/crystal minerals scattered around the map, carry
# them to YOUR OWN bin (the collector), bank points on deposit; dying drops
# everything as a stealable pile. All authored data is static (Data.dat
# census 2026-07-19, parsed by zaxbot/sk_data.py, pinned in tests):
#   * minerals are CEntityBase parts with Model=Items/Money/{Ore deposit N,
#     Crystal NN}, Used In=MultiPlayer/Salvage King (SK matches ONLY),
#     respawn-in-place 10-15 s, and are DENSE (107..386 per map, 1856 across
#     the 9 SK-capable maps — the 8 Greed maps + Jungle Ruins);
#   * bins are 'Bin NN' CEntityAnimated CollideTrigger parts whose canned
#     deposit action ('Drop Ore in Container') gates on CIsOnSameTeamAction —
#     the authored Team Number == NN-1 covers [0, MaxPlayers) contiguously,
#     the SAME id space the patch already assigns SK bots (team = botidx), so
#     each bot's one scoring bin is known statically per map.
# Behavior model (armed per match when detect_mode()==SK with a graph + SK
# data — sk_routing_active):
#   * COLLECT phase (carried minerals < SK_RETURN_CARRY_MIN): descend the
#     per-match MULTI-SOURCE mineral field (sk_ore_dist — one bfs_run seeded
#     with every mineral-bearing node at distance 0, built once per match:
#     minerals respawn in place, so presence tracking is deliberately NOT
#     attempted) toward the nearest mineral zone; INSIDE a zone (dist == 0)
#     fall back to the random roam, which sweeps the dense cluster and
#     collects by walk-over overlap.
#   * RETURN phase (carried >= the bot's rolled threshold, latched until the
#     deposit empties the inventory): descend this bot's own-bin row
#     (sk_bin_dist, one bfs_run per authored bin at match start, indexed by
#     team id) and
#     final-approach the bin center at its node — bins are collidable props,
#     so the press machinery (watchdog + patience) fires the CollideTrigger
#     exactly like the switch wander-bump; the engine's canned action does
#     the actual scoring/consuming, which flips the carry state and ends the
#     approach naturally.
SK_ENABLED = True
# Deposit threshold: a bot heads home once carrying this many minerals total
# (Ore Deposits + Crystals). RANDOMIZED per bot per run: each bot rolls a
# fresh threshold in [LO, HI] via the engine RNG — lazily when it first
# picks something up, and again every time a banked run completes (the
# RETURN->empty transition in sk_update_phase, which fires the moment the
# deposit scores; a death while latched re-rolls too — new life, new plan).
# Low rolls = frequent short banking runs (safe, less efficient); high
# rolls = long greedy runs that drop a fat stealable pile on death. The
# engine awards score per deposited mineral, so this is pacing, not value.
# LO must stay >= 1 (0 is the per-bot "unrolled" sentinel).
SK_RETURN_CARRY_RAND_LO = 30
SK_RETURN_CARRY_RAND_HI = 100
# Live per-map mineral table (The Foundry authors 386 — the shipped max).
SK_MINERAL_TABLE_MAX  = 400
# Bins live table is indexed by TEAM id (== bin number - 1); 16 = MAX_BOT_SLOTS
# and the shipped per-map max (The Foundry).
SK_BIN_TABLE_MAX      = 16
SK_STATIC_MAP_MAX     = 12   # shipped Data.dat has 9 SK-capable maps
SK_STATIC_MINERAL_MAX = 1920 # shipped Data.dat has 1856 mineral anchors
SK_STATIC_BIN_MAX     = 80   # shipped Data.dat has 70 bins
SK_MAP_NAME_SLOT      = 96   # fixed ASCII bytes per map path, incl. NUL
# Bin final-approach press patience (mirror of the switch/pad/door patience):
# fresh watchdog cycles pressing the bin's collide trigger before the deposit
# attempt suspends routing (roam) and retries later. The trigger re-fires
# every 0.5 s while overlapping, so 2 cycles (~2 s) is ample for a clean
# deposit; truly-wedged approaches suspend like every other final approach.
SK_DEPOSIT_PRESS_PATIENCE = 2
# --- SK death piles (the stealable "pile of ores and crystals") -----------
# Every MP death runs the canned 'Drop Cystals and Ore' (sic) script whose
# CDropAllOreAndCrystalsAction (apply sub_5A6E60) clones a NAMELESS
# CEntityAnimated pile from the Ore_Crystals01 model template, placed
# collision-aware within 500 px of the corpse, holding the victim's whole
# load; TOUCHING it grants everything to either team and self-deletes. There
# is no entity name to match (unlike CTF flag drops), so detection hooks the
# drop apply itself (mirror of the portal self-registration): the detour
# records the DYING CHARACTER's position into a small ring table — skipping
# empty-handed deaths via the engine's own carried-count getter (sub_426860,
# which the apply also gates on) — and pursuit steers at that point (the
# placement lands at/near the corpse in open ground, which is where fights
# happen). Entries expire by TTL, on a bot reaching the spot (optimistic
# clear — the touch consumed it), and on match change; a pile a HUMAN
# grabbed leaves at worst a TTL-bounded stale entry that costs a bot one
# short revisit. Pursuit is opportunistic: a bot passing within
# sqrt(SK_PILE_PURSUE_RADIUS_SQ) diverts straight at the pile through the
# standard watchdog + patience, with a cooldown after success/failure so
# nobody orbits an unreachable spot.
SK_PILE_TABLE_MAX = 8
SK_PILE_PURSUE_RADIUS_SQ  = 300.0 * 300.0
SK_PILE_REACHED_RADIUS_SQ = 24.0 * 24.0
SK_PILE_PRESS_PATIENCE    = 2
SK_PILE_RETRY_COOLDOWN_FRAMES = 240
SK_PILE_GRAB_COOLDOWN_FRAMES  = 150
# Registered piles expire after this many frames (~45 s at 60 Hz) if nothing
# collected them — SK piles rarely survive longer, and the bound keeps stale
# human-grabbed entries from pulling bots forever.
SK_PILE_TTL_FRAMES = 2700

# --- Generalized GOODY pursuit (graph-routed; piles + filler items) --------
# The original pile pursuit was straight-steer only — a pile spawned across a
# wall within the 300 px entry radius made the bot grind the wall until
# patience ran out (user-reported). Pursuit is now TWO-PHASE like the CTF
# dropped-flag v2: while latched and farther than GOODY_DIRECT_RADIUS_SQ,
# sk_next_hop descends a BFS field to the target's graph node (walls are
# routed AROUND, teleport pads hop exactly like every other descent); the
# straight steer runs only inside the direct radius or on the target's own
# bound node. Fields:
#   * piles — sk_pile_dist, MULTI-SOURCE over the live pile ring's bound
#     nodes; rebuilt event-driven (registration, TTL expiry, a bot grabbing
#     one) via the sk_pile_dirty flag on the page flip.
#   * filler items — item_dist, one multi-source row per CATEGORY
#     (health/energy/shield, model-prefix classified by item_data.py; 272
#     anchors across all 17 MP maps), built once per match — fillers respawn
#     in place, so like minerals there is no presence tracking; a
#     just-consumed anchor costs one cooldown-bounded empty visit.
# Item pursuit is OPPORTUNISTIC and works in EVERY mode (DM/CTF/SK): a bot
# passing within sqrt(ITEM_PURSUE_RADIUS_SQ) of a filler diverts to it,
# gated by the shared per-bot goody cooldown so nobody chain-diverts. The
# per-think target is re-resolved as the NEAREST pile / nearest item of the
# latched category, so a category descent that reaches a closer item of the
# same kind simply takes it.
ITEM_PURSUIT_ENABLED = True
ITEM_PURSUE_RADIUS_SQ = 250.0 * 250.0
# NEED GATE (user-reported greed, 2026-07-22: "if the bot has full health it
# should ignore health items", same for shield/energy, and "no shield at all
# -> ignore shield blobs entirely"): before every goody item scan the
# follower refreshes goody_need_mask (bit0 health / bit1 energy / bit2
# shield) from the bot's LIVE state, and the scan skips categories whose
# bit is clear — so un-needed fillers are never latched, and a latched
# category whose need disappears mid-route (topped up by another pickup)
# resolves to no target and unlatches cleanly. The tests are the ENGINE'S
# OWN pickup-useful predicates, not re-derived rules: health =
# cur_damage(char+0x7C) != 0; energy = SUB_BATTERY_NEED_VA (carried battery
# charge < capacity; no battery -> no need); shield = SUB_SHIELD_NEED_VA
# (carried shield charge < capacity; NO SHIELD CARRIED -> NO NEED — the
# exact "don't target shield blobs without a shield" rule). Off -> the old
# opportunistic-always behaviour.
ITEM_NEED_GATE_ENABLED = True
# After grabbing (or failing) an item divert, no new goody latch for this
# many thinks (~5 s) — also spaces out revisits to a consumed anchor.
ITEM_GRAB_COOLDOWN_FRAMES = 300
# Shared two-phase knobs (piles AND items): straight-steer only within
# sqrt(DIRECT); silently drop a latch whose target drifted beyond
# sqrt(ABANDON) (routed paths legitimately move AWAY from the target around
# walls, so keep this comfortably above the entry radii).
GOODY_DIRECT_RADIUS_SQ  = 160.0 * 160.0
GOODY_ABANDON_RADIUS_SQ = 600.0 * 600.0
# Live per-map goody table (Caves of Gold / The Foundry author 71
# fillers+weapons each — the shipped max since the weapon category landed).
ITEM_TABLE_MAX        = 80
ITEM_STATIC_MAP_MAX   = 20   # shipped Data.dat has 17 MP goody maps
ITEM_STATIC_POINT_MAX = 512  # shipped Data.dat has 492 anchors (272 fillers + 220 weapons)
ITEM_MAP_NAME_SLOT    = 96   # fixed ASCII bytes per map path, incl. NUL
ITEM_CATEGORIES       = 4    # health / energy / shield / weapon (item_data.ITEM_CAT_*)

# WEAPON pickups as a pursuit category (user-requested 2026-07-23: "bots
# should prioritize weapon pickups over ammo and other regular things,
# except the dropped flags / flag carrier / dropped pile"). Category 3 =
# gun-GRANTING pickups only (item_data's explicit model set — ammo packs
# stay walk-over-only; PU Light Pistol IS included because the actual
# spawn loadout is the far weaker Modified Laser Welder). Ranking: the
# entry scan tries piles (SK), then WEAPONS within their own (larger)
# radius, then the any-category filler fallback — while the CTF drop
# pursuit, the enemy-carrier chase and the pad approach still outrank the
# whole goody block by dispatch order, exactly the exception list the
# user gave.
# The weapon entry is a DISTANCE-WEIGHTED ROLL (user rule 2026-07-23:
# "the closer the weapon, the higher the chance"): chance =
# WEAPON_PURSUE_CHANCE_MAX * (R^2 - d^2) / R^2 — an adjacent gun is a
# near-certain grab, one at the radius edge almost never diverts, and an
# attacker en route keeps re-rolling with rising odds as its path closes
# on the pickup. A failed roll arms the shared goody cooldown for
# WEAPON_ROLL_RETRY_FRAMES (short) instead of the full grab cooldown.
# Need gate: a bot "needs a weapon" while it carries fewer than
# WEAPON_NEED_MIN_OWNED Primary-group items (spawn loadout = the lone
# welder, so fresh bots hunt guns and armed bots stop detouring); the
# bit shares the goody_need_mask machinery (bit3).
WEAPON_PURSUE_RADIUS_SQ   = 350.0 * 350.0
WEAPON_PURSUE_CHANCE_MAX  = 100
WEAPON_ROLL_RETRY_FRAMES  = 45
WEAPON_NEED_MIN_OWNED     = 3

# WEAPON AUTO-EQUIP (user-reported 2026-07-23: "most of the fight happens
# with the [starter] Modified Laser Welder" — bots picked up better guns
# but nothing ever SELECTED them, so every fight stayed on the welder).
# A page-flip pass checks each live bot every WEAPON_EQUIP_CHECK_FRAMES:
# if the SELECTED Primary item is the welder (def key resolved per match)
# and the bot carries another Primary weapon whose can-fire gate passes
# (item vtbl+0x98 — has ammo, off delay), it is selected via the
# spawn.py force-switch sequence. Bots on a working real gun are left
# alone (no churn); if every carried gun is empty the welder stays (the
# engine's own fire path auto-cycles on empty anyway).
WEAPON_EQUIP_ENABLED      = True
WEAPON_EQUIP_CHECK_FRAMES = 90

