"""Lava (plasma) avoidance: proactive heading veto + reactive flee."""

# --- Proactive lava (plasma) avoidance ------------------------------------
# Molten maps render lava as "Plasma Ground" (engine class CPlasmaTileMap): a
# 64px tile grid whose heat/elevation grid (CPLASMA_HEAT_OFF) holds a 0..255
# value per tile. R-snapshot census on Molten Ice: the walkable ambient floor
# reads <=127, the damaging molten pools ramp 128..255 (host burned at 221),
# so heat >= 128 is the natural "this tile is lava" boundary. scan_plasma
# captures the map per match; the movement detour samples the heat grid a short
# distance ahead along the bot's heading and, if it would step into lava,
# rotates the heading (like the wall-slide) until a lava-clear direction is
# found. plasma_map == 0 (non-plasma maps) makes the whole thing a no-op.
# DISABLED by default. The per-frame heading veto (rotate away from lava)
# fights the waypoint follower (which steers by the same emitted angle): on a
# lava-heavy map the lookahead constantly pokes into the central molten mass,
# so the veto deflects the bot off its waypoint path and into nearby walls
# ("moves opposite / doesn't follow waypoints / sticks at random walls").
# Detection (scan_plasma / is_plasma_at) is confirmed working and stays wired
# for diagnostics; the correct avoidance is GRAPH-AWARE (author waypoints on the
# safe ambient floor and reject lava-crossing edges via is_plasma_at), which
# routes around lava without overriding the heading. Re-enable only with a small
# LAVA_LOOKAHEAD_PX for last-moment edge nudging, or after graph-aware routing.
LAVA_AVOID_ENABLED = False         # master switch for the per-frame heading veto
# Heat value at/above which a tile counts as damaging lava. 128 is conservative
# (gives the pools a wide berth, including the warm 128..191 ring); raise toward
# ~192 if bots route too timidly around lava, lower if they graze it.
LAVA_HEAT_THRESHOLD = 160
# How far ahead (world px) to sample along the emitted heading. ~0.75 of a tile
# so the bot vetoes the next tile it would enter before reaching it.
LAVA_LOOKAHEAD_PX = 32.0
# Heading sweep step (degrees) when the lookahead hits lava: rotate by this each
# try, up to a full circle, until a lava-clear heading is found. Mirrors the
# wall-slide step; 30 deg * 12 tries = 360.
LAVA_SWEEP_STEP_DEG = 30.0

# --- Reactive lava flee (the ACTIVE lava behaviour) -----------------------
# Lava is walkable and depletes HEALTH fast (Cur Damage at char+0x7C rises;
# shield is bypassed). So the bot reacts to HEALTH damage: whenever cur_damage
# rises it just stepped on something health-harmful (lava/fire), and it
# REVERSES its emitted heading for a short window — backing off the way it came
# (onto the authored safe ground) — then resumes waypoint following. Re-armed
# every frame damage continues, so the bot keeps backing off until clear. This
# is isolated from the wall-slide/waypoint logic (it only negates the emitted
# vector), so it can't wedge the bot on walls. Closed damaging gates are handled
# separately: they physically BLOCK the bot, so the existing progress watchdog
# retreats to the previous node and reroutes to a different neighbour.
LAVA_FLEE_ENABLED = True
# Frames to keep reversing after the last health-damage frame (~0.25s at 60Hz).
# Higher = backs off further / more committed; lower = snappier re-evaluation.
LAVA_FLEE_FRAMES = 15

# Waypoint editor: when dropping a new node, snap to an existing node if
# within this world-pixel distance (squared) — avoids duplicate nodes when
# re-walking the same corridor. 24 px ≈ collision radius scale.
WP_SNAP_RADIUS_SQ      = 24.0 * 24.0

# Waypoint persistence: per-map files saved to <WP_DIR>/<map_name>.zwpt
# (map name read from MAP_NAME_CSTRING_VA at runtime; '/' and '\\' in the
# name are sanitized to '_'). Directory is auto-created on save.
WP_DIR                 = b'waypoints'
WP_FILE_SUFFIX         = b'.zwpt'


