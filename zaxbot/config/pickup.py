"""Proximity pickup registration + the (dormant) stage-2 pickup divert."""

# --- Proximity item pickup (bots grab nearby items) ----------------------
# Stage 1 (detection): a detour on sub_53DA40 — the per-frame CPickupAI
# update, which runs once per pickup entity per frame — records each live
# pickup's world position into pickup_table. The overlay draws those as
# orange markers so detection can be verified in-game before any bot
# behavior is wired (stage 2). Pickups are otherwise NOT enumerable: they
# are CPickupAI grid components, not entries in any flat array (mgr+0x290 is
# players, mgr+0x2BC is layers), and the engine's spatial query is masked to
# blocking entities only. See the [[pickup-enumeration]] memory.
PICKUP_REGISTER_ENABLED = False
# Install the pickup self-registration detour for visual item markers, but
# keep the scratch flag disabled until the overlay is visible. The O-key
# toggle turns registration on/off alongside overlay drawing so normal play
# does not keep populating pickup_table every frame.
PICKUP_OVERLAY_MARKERS_ENABLED = True
# Max pickups tracked per frame (each slot is 8 bytes: x, y floats). Sized to
# the Data.dat census (2026-07-19): SK mode loads every mineral, and The
# Foundry peaks at 502 total pickups (386 minerals + the weapon/ammo/health
# mix); EVERY SK map exceeds the old 96 cap (min 135), which was the reported
# "only ~80-90% of ores are marked" overlay gap — pickups past the cap simply
# never registered. Non-SK modes stay well under (max: Battle on the Ice 74).
PICKUP_TABLE_MAX        = 512

# --- Stage 2: bots occasionally divert to grab a nearby pickup -----------
# Master switch. When False the movement detour is behaviorally identical to
# pure waypoint following (the divert block fast-skips on one cmp/jz). Enable
# this together with PICKUP_REGISTER_ENABLED when testing item-divert behavior;
# both are off by default so normal patched gameplay does not hook every pickup
# update on every frame.
PICKUP_DIVERT_ENABLED   = False
# A bot only diverts to a pickup within sqrt(this) of itself (squared px).
# 250px = "moderate" eagerness — close/on-the-way items, not the whole map.
PICKUP_DIVERT_RADIUS_SQ = 62500.0
# The bot "reached" the pickup (ends the divert) when within sqrt(this). Keep
# near the collision/collect overlap so the engine auto-grants the item as the
# bot arrives; too large and the bot stops short without collecting.
PICKUP_REACHED_RADIUS_SQ = 576.0          # 24 px
# After grabbing (or abandoning) a pickup, the bot follows waypoints for this
# many frames before it will divert again — gives the "occasionally" cadence
# and prevents re-diverting onto a spot it just cleared. 180 ≈ 3 s at 60 Hz.
PICKUP_COOLDOWN_FRAMES  = 180
# Hard cap on a single divert. If the bot can't reach the latched pickup within
# this many frames (e.g. it's across a wall — there is no LOS check in v1), it
# abandons and resumes the graph. A wall-wedge is usually caught much sooner by
# the shared stuck detector (STUCK_FRAMES_THRESHOLD); this is the backstop.
PICKUP_DIVERT_TIMEOUT_FRAMES = 150
# Reactive hazard (lava) avoidance — now a FALLBACK behind the proactive
# plasma-tile detection below. Watches the bot's accumulated damage (char+0x7C):
# if it rises while diverting — or just before starting one — the bot just
# stepped on something harmful (lava/fire/weapon fire), so it abandons the
# divert, takes the cooldown, and follows the waypoint graph back off the
# hazard. Kept as belt-and-suspenders for the 1-frame edge case the proactive
# veto can't pre-empt (already on lava at spawn, knockback, etc.). Set False to
# let bots grab items regardless of damage.
PICKUP_DIVERT_AVOID_DAMAGE = True

