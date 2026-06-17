"""Bot-policy configuration: section layout, scratch sizes, bot names, and the
synthetic DirectPlay id range used by Phase B's queue-injection spawn flow.

These are all knobs that can change without re-reverse-engineering the engine
(unlike `addresses.py`). Keep raw engine VAs out of this module.
"""

from .build import SectionSpec


# --- new section parameters (.zaxbot) -------------------------------------
NEW_SECTION_NAME   = b'.zaxbot\x00'
NEW_SECTION_VA     = 0x31A000      # RVA; absolute = 0x71A000
NEW_SECTION_SIZE   = 0x8000        # eight pages: 16KB code + 16KB scratch (grew from 7 for save/load)
SECTION_CHARACTERS = 0xE0000020    # CODE | EXEC | READ | WRITE
HOOK_ENTRY_OFF     = 0x000
SCRATCH_OFF        = 0x4000        # writable scratch buffer; 16KB code / 16KB scratch

ZAXBOT_SECTION = SectionSpec(
    name=NEW_SECTION_NAME,
    rva=NEW_SECTION_VA,
    size=NEW_SECTION_SIZE,
    characteristics=SECTION_CHARACTERS,
)

# --- Tagged-chunk format for zax_dump.bin --------------------------------
# Each chunk: magic | tag(16B, zero-padded ASCII) | src_va | len | payload[len].
DUMP_MAGIC       = 0x3158415A             # 'ZAX1' as bytes 5A 41 58 31 in memory (LE dword)
DUMP_TAG_LEN     = 16
DUMP_HEADER_SIZE = 4 + DUMP_TAG_LEN + 4 + 4  # = 28 bytes

DUMP_FILENAME = b'zax_dump.bin\x00'
DUMP_MSG      = b"bot: spawned\x00"
FULL_MSG      = b"bot: match full\x00"
STEP_FILENAME = b'zax_step.log\x00'   # one-letter progress markers, flushed per step

# --- Bot fire/aim policy -------------------------------------------------
FIRE_RANGE_SQ = 90000.0   # squared distance; bot fires when host is within sqrt(FIRE_RANGE_SQ)

# Projectile speed used as the last-resort fallback for shot leading. Units
# are engine-world units per pick_target call (≈ per frame), matching the
# per-frame delta the perception loop uses to estimate target velocity. Only
# reached if both the manual WEAPON_SPEEDS override and the dynamic def-read
# fall through (e.g. NULL weapon or NULL item-def). Hitscan weapons (no
# projectile) bypass apply_lead entirely and ignore this value.
PROJECTILE_SPEED = 10.0

# Conversion factor applied to the engine's per-weapon raw "Move/Max
# Velocity" field (pixels per second, schema range ~300..4000) to bring it
# into the per-pick_target-call units apply_lead's time math expects.
# Default 1/60 because the engine main loop passes a fixed 1/60 tick; if frames
# slow down, the dt term cancels algebraically in
# `lead = vx*t = (real_vel*dt) * (dist/(raw*dt))`, so one tuned constant
# covers every weapon. Calibrate empirically: spawn a bot with Missile
# Launcher (slow projectile), watch a strafing host, halve/double until
# shots land. See docs/04-spawn-ai-leads.md "Calibration recipe".
SPEED_SCALE = 1.0 / 60.0

# Muzzle spawn offset (pixels) — the engine spawns projectiles at the gun
# barrel tip, which is some distance in front of the bot's character center
# along the firing angle. Without compensation, our lead math over-predicts
# the bullet's flight distance by this amount (bullet arrives at the aim
# point sooner than expected ⇒ target hasn't moved as far ⇒ bot over-leads
# by `vel * muzzle / proj_speed` pixels per shot).
#
# Confirmed empirically: with a stationary bot firing Missile Launcher
# (CModel velocity = 800 px/sec), the captured projectile entity's spawn
# position back-extrapolated to ~20 px from the bot center. Calibrate by
# testing with a strafing host: too-high MUZZLE_OFFSET makes the bot UNDER-
# lead (bullets land behind target); too-low MUZZLE_OFFSET retains the
# original OVER-lead.
#
# Applied as a constant across all weapons. CProjectileInfo at def+0x44
# stores per-weapon X/Y offsets if a more precise fix is needed later;
# for now one global constant covers the typical case.
MUZZLE_OFFSET = 20.0

# Probability that the bot applies projectile lead on a given fire-tick.
# 0.0 = always shoot at target's current position (no prediction at all),
# 1.0 = always lead (perfect tracking), 0.5 = coin-flip per shot.
#
# Why randomize: perfect lead feels robotic — every shot lands the same way
# and a human player can learn to micro-zigzag to dodge consistently. A
# 50/50 mix gives bots a more human feel: half the shots predict your
# motion, the other half just shoot where you are right now, so dodging
# requires actual reflexes rather than a fixed counter-strategy.
#
# Hitscan weapons (Semi Auto Pistol, Alien Electrical Weapon) ignore this
# knob — they're instant-hit so leading is meaningless either way.
LEAD_PROBABILITY = 0.6

# Per-weapon projectile-speed OVERRIDE table, keyed by the weapon's inventory-
# item definition pointer returned by sub_4DD480(current_weapon_obj). Looked
# up first by compute_proj_speed; on a hit the override wins. On no match
# the dispatcher falls through to a runtime read of [def + 0x20] (projectile
# prototype) and [proto + 0x60] (Move/Max Velocity), scaled by SPEED_SCALE.
# That dynamic path handles every standard weapon out of the box — leave
# this list empty unless you need to pin a specific weapon's speed for
# tuning. Populate by observation: equip/fire a weapon, press R, read the
# `current_proto_va` dword from the `weapon_info` snapshot chunk, and add a
# `(definition_va, speed)` entry here.
#
# HITSCAN OVERRIDE: use speed = 0.0 as the sentinel — the dispatcher will set
# is_hitscan and skip apply_lead entirely, even if the engine's def has a
# (perhaps very fast) projectile prototype.
WEAPON_SPEEDS = []  # type: list[tuple[int, float]]
# Slots reserved in scratch for the runtime lookup; bumping this only costs
# bytes in .zaxbot scratch, so keep some headroom.
WEAPON_SPEEDS_MAX = 32

# --- Bot movement / wander policy (DM-only first pass) -------------------
# Master switch — set False to revert to the original zero-vector behavior
# (bots stand still). Useful for A/B-comparing fire/aim regressions.
MOVEMENT_ENABLED = True

# Bot picks a random world-space target within ±WANDER_TARGET_RADIUS px of
# its current position, walks toward it, then re-rolls when the timer expires
# or stuck detection trips. Targets aren't bounded to the map — out-of-bounds
# picks are corrected by stuck detection retargeting within a second or so.
WANDER_TARGET_RADIUS         = 600.0
WANDER_TARGET_TIMEOUT_FRAMES = 600     # ≈10 s at 60Hz

# Stuck detection: a frame counts as "not moving" when (x-last_x)²+(y-last_y)²
# is under STUCK_DELTA_SQ. Used by the random-wander fallback; waypoint
# following has its own progress-to-node timeout below. CRITICAL: this must be
# BELOW the bot's normal step (~1.7px/frame) or a steadily-walking bot is
# falsely tagged stuck. 0.25 (0.5px/frame) means only a truly-stationary bot
# accumulates.
STUCK_FRAMES_THRESHOLD = 30
STUCK_DELTA_SQ         = 0.25

# Mild item attractor: if a CPickupAI entity is within sqrt(radius_sq) and
# line-of-sight passes, blend WEIGHT * (pickup - bot) into the movement
# vector. sub_4303F0 (engine collision) handles the actual pickup on
# walk-over, so the attractor only nudges; it doesn't need to land on the
# pixel. Scan is staggered per bot at ITEM_SCAN_INTERVAL_FRAMES to spread the
# entity-iteration cost across frames.
ITEM_ATTRACTOR_RADIUS_SQ   = 40000.0   # 200px reach
ITEM_ATTRACTOR_WEIGHT      = 0.7
ITEM_SCAN_INTERVAL_FRAMES  = 30

# Proactive hazard avoidance: at match start, scan world entities of class
# CDamageExpandingRadiusAI and cache (x, y, default_radius_sq). The movement
# detour then treats each cached hazard as a repulsor scaled by inverse
# distance, blended into the base direction.
#
# Currently DORMANT — the entity-array offset hypothesis (mgr+0x2BC/0x2C0)
# was wrong (that field pair only stores 1 active char, not the full entity
# list), so the proactive scan finds nothing. The reactive cur_damage-based
# avoidance below is the active hazard-handling path. Re-enable once the
# real entity iterator is identified.
HAZARD_REPULSION_RADIUS_SQ = 90000.0   # 300px reach
HAZARD_REPULSION_WEIGHT    = 2.0
HAZARD_DEFAULT_RADIUS_SQ   = 90000.0   # used as the per-entity bubble until we read it off the AI

# Reactive hazard avoidance: when `[char+0x7C]` (cur_damage) increases, the
# bot took damage from SOMETHING — lava, fire, projectile, etc. We
# immediately bias the wander target opposite to the bot's recent motion,
# commit to the flee for HAZARD_FLEE_FRAMES, and suppress stuck-based and
# timer-based retargets during the flee. This gives the bot a deliberate
# back-off rather than a 50/50 random retarget (which would keep the bot
# wandering back onto lava). Higher values commit longer to the flee
# direction; lower values resume wander sooner.
HAZARD_FLEE_FRAMES = 120

# Per-frame velocity magnitude written to the movement vector after
# normalize. The engine reads our |v| and computes a ratio
# `(|v| - model_min) / (model_max - model_min)` clamped to [0, 1], then
# scales position by that ratio against the model's per-frame step. Without
# knowing the bot model's exact min/max bounds, this knob is empirical:
# too high ⇒ engine clamps to full-speed (bot teleports off the map and
# crashes the collision lookup); too low ⇒ engine sees |v| < min and
# treats as no movement.
#
# 1.0 = the human player's full movement speed. The engine's walking model is
# NORMALIZED to [0,1]: the original sub_542360 (the host's own movement path)
# emits a velocity whose magnitude saturates at exactly 1.0 while a movement key
# is held (it clamps an input term to a6 then divides by a6 → ≤1.0). sub_543B60
# then maps that magnitude through the walk/run animation tiers, and its run-tier
# ramp reaches a full per-frame step (v72=1.0) only at |v|=1.0. So a bot that
# emits |v|=1.0 moves at exactly the host's speed — which is the goal (emulate
# real players). 3.0 was 3× the player max (the "too fast" report); the 3.0 in
# static_data.py is a stale pre-normalization default. (The "300..4000 px/sec"
# in AGENTS.md is the unrelated *projectile* CModel max at proto+0x60, not this
# model.) Drop a touch below 1.0 only for deliberately slower-than-host bots.
BOT_MOVE_SPEED = 1.0

# --- Waypoint following (graph navigation) -------------------------------
# Master switch. When True AND a graph is loaded for the current map
# (overlay_vertex_count > 0), bots steer straight at the current waypoint node
# and advance along real edges. When False, or on a map with no graph, bots
# idle — the old random-wander/attractor potential field that perturbed the
# heading into walls was REMOVED. This is the panic switch for the follow
# feature (mirrors MOVEMENT_ENABLED, which gates all bot movement on top).
WP_FOLLOW_ENABLED = True

# At a junction (a node with several connected edges) pick a RANDOM connected
# neighbour (preferring one that isn't the node we just came from) so bots roam
# the whole graph instead of all taking the first branch. Set False for a
# deterministic "first non-prev neighbour" choice (reproducible R-dumps).
WP_RANDOM_NEIGHBOR = True

# Wall-slide: the engine moves a bot purely by the emitted ANGLE and refuses to
# move it at all when that angle points into geometry (no auto-slide). So when
# a bot is physically wedged for a few frames, the follower sweeps the emitted
# angle by this many degrees per ramp step until a heading clears the wall and
# the bot slides along it (then the deflection decays back to straight-at-node).
# Smaller = finer/smoother sweep but slower to escape; bigger = faster escape
# but coarser. ~30 deg with the in-asm ramp cap (11) sweeps ~330 deg, enough to
# clear any blocked half-plane.
WP_SLIDE_TURN_STEP_DEG = 30.0

# Radius-based arrival: a bot "reaches" its current target vertex when the
# squared distance to it drops below this, at which point it advances to the
# next node along an edge. This is the PRIMARY (and only) arrival test — a
# plane-cross-only test was tried before and BROKE following, because engine
# collision routinely stops a bot a few pixels short of the node so it never
# crosses the plane and never advances. Keep this comfortably larger than the
# bot's collision radius so a bot wedged near a corner still counts as
# arrived. R snapshots on Molten Ice showed bots physically wedged 44px from a
# corner waypoint, so 40px was too strict; 64px is still below the long corridor
# waypoint spacing but large enough to accept collision-limited corner arrival.
WP_REACHED_RADIUS_SQ = 4096.0

# --- Edge following (hug the connection line) ----------------------------
# When latched (prev->current edge known), steer toward a point ON the
# prev->current segment instead of straight at the node, so the bot converges
# back onto the connection line after any drift (flee, wall-slide, early node
# advance) rather than cutting diagonally. This is critical on narrow lava
# corridors where "a few px off the line" means stepping into lava. Set False
# to revert to straight-at-node steering (the bot will follow more loosely).
WP_EDGE_FOLLOW_ENABLED = True
#
# Look-ahead as a FRACTION of the current edge length: the bot targets its own
# projection onto the segment plus this much further toward `current` (clamped
# to the segment, never past the node, so it can't corner-cut onto the next
# edge). Smaller = hugs the line tighter (slower to advance); bigger = leads
# more toward the node. ~0.15 is a smooth lead; drop toward 0 for the tightest
# line-hugging on very narrow paths.
WP_EDGE_LOOKAHEAD = 0.15

# --- Off-graph recovery (progress-timeout escape) ------------------------
# The bot routinely ends up physically SEPARATED from its target node by a
# wall — a bad spawn, a lava-death respawn into a pocket, or explosion/player
# knockback drops it off the authored graph. With pure edges-only steering and
# no recovery it then PINS forever: it micro-oscillates against the wall (so
# stuck_count, thresholded on raw movement, never climbs) while making zero
# progress toward its target. R-dumps showed exactly this — a latched bot
# frozen 328px off its edge in a corner across many seconds, and a committed
# bot dying on lava 245px short of an unreachable node.
#
# The fix is a PROGRESS-based pin-detector (immune to the micro-oscillation
# that defeats stuck_count): track the minimum distance² to the current target
# node achieved so far; every frame that doesn't strictly beat that minimum
# increments a stall counter. When the bot has made no real progress for this
# many frames it is genuinely wedged off-graph, so it first re-acquires the
# nearest node from the bot's current position. If that nearest node is the same
# failed target, it runs a brief random-wander relocate burst before trying
# again. Edge-following while genuinely progressing is untouched.
# ~2.5s at 60Hz. Lower = recovers faster but may interrupt legitimately-slow
# progress (squeezing past the host); higher = visible pin before recovery.
WP_PROGRESS_TIMEOUT_FRAMES = 30

# Length of the random-wander relocate burst used only when progress timeout
# cannot find a different nearest node. The burst is steering-only: it never
# teleports the bot. When the countdown ends, the follower re-acquires the
# nearest node from the new position.
WP_RELOCATE_FRAMES = 150

# --- Testing knob: force-equip every freshly-spawned bot with this item -----
# Bots default to whatever the engine's "Players Initial Inventory" gives
# them -- and since they don't move, they can't pick up replacements. These
# knobs are the safest way to exercise per-weapon prediction with different
# weapons. This is configured by inventory item *name* (the same names the
# engine's XmasShopping cheat uses), not by the per-character local item index.
#
# Use DEBUG_BOT_WEAPON_NAMES for a curated local test list, then select one
# entry with DEBUG_BOT_WEAPON_INDEX.
DEBUG_BOT_WEAPON_NAMES = []  # type: list[str]
DEBUG_BOT_WEAPON_INDEX = None  # type: int | None

# Valid weapon names from the binary's key-binding dialog (sub_59C550 entries
# 28..42 at 0x59C550) — the 15 playable firearms that can be bound to a hotkey
# and carried in the primary/secondary slots:
#   'Modified Laser Welder'
#   'Light Pistol'
#   'Twin Disruptor'
#   'Semi Auto Pistol'
#   'Full Auto Pistol'
#   'Grenade Launcher'
#   'Nuclear Disruptor'
#   'Impaction Cannon'
#   'Alien Electrical Weapon'
#   'Tri Spread Gun'
#   'Missile Launcher'
#   'Heavy Barrell'
#   'Mega Fusion Disruptor'
#   'Plasma Thrower'
#   'Psyonic Wave Glove'
# Additional weapon-like items emitted by the XmasShopping cheat (sub_5A22C0)
# that may also resolve via sub_4DD480 — single-player / special pickups:
#   'Autogun Medium', 'Proximity Mine', 'sphere of zin', 'Globe of Prayer',
#   'Knife of Sacrifice', 'Reflecting Staff', 'Ring of Fire', 'Staff of Air',
#   'Plasma Canister'
# Direct one-off override. If set, this takes precedence over the debug list.
# When non-None, the spawn path also stuffs the bot with the items listed in
# FORCE_BOT_AMMO_NAMES below (max battery cap + energy refill + every ammo
# pool) so the forced weapon has full ammo immediately on spawn.
FORCE_BOT_ITEM_NAME = None  # type: str | None

# Items handed to the bot on spawn whenever FORCE_BOT_ITEM_NAME is set. Order
# matters: 'Battery Level 3' is the max battery-cap upgrade item (the highest
# tier the engine ships — there is no Battery Level 4 string in the binary),
# and each subsequent 'Battery Charge' tops up the energy pool that hosts the
# energy-based weapons' ammo. The remaining entries cover every non-energy
# ammo pool so the loaded weapon, whatever it is, has full ammo on spawn.
# Each name must be a valid inventory-item-definition name (i.e. resolvable
# via sub_523DF0(name, -1) against the item-definition registry).
FORCE_BOT_AMMO_NAMES = [
    'Battery Level 3',
    'Battery Charge',
    'Battery Charge',
    'Battery Charge',
    'Battery Charge',
    'Battery Charge',
    'Battery Charge',
    'Bullets',
    'Bullets',
    'Bullets',
    'Missiles',
    'Missiles',
    'Missiles',
    'Crystals',
    'Crystals',
    'Ore Deposits',
]
# Scratch capacity for the ammo list. Each slot is 32 ASCII bytes.
FORCE_BOT_AMMO_MAX        = 32
FORCE_BOT_AMMO_SLOT_SIZE  = 32


def _validate_bot_item_name(value, label):
    if type(value) is not str:
        raise ValueError(f'{label} must be a string item name, got {value!r}')
    data = value.encode('ascii')
    if not data:
        raise ValueError(f'{label} must not be empty')
    if b'\x00' in data:
        raise ValueError(f'{label} must not contain NUL bytes')
    return data + b'\x00'


def resolve_force_bot_ammo_names():
    """Return the configured FORCE_BOT_AMMO_NAMES as a list of NUL-terminated
    ASCII byte strings, or an empty list if FORCE_BOT_ITEM_NAME is None.

    Each returned entry fits within FORCE_BOT_AMMO_SLOT_SIZE bytes including
    the terminator. The total count is bounded by FORCE_BOT_AMMO_MAX.
    """
    if FORCE_BOT_ITEM_NAME is None:
        return []
    if type(FORCE_BOT_AMMO_NAMES) is not list:
        raise ValueError('FORCE_BOT_AMMO_NAMES must be a list of strings')
    if len(FORCE_BOT_AMMO_NAMES) > FORCE_BOT_AMMO_MAX:
        raise ValueError(
            f'FORCE_BOT_AMMO_NAMES has {len(FORCE_BOT_AMMO_NAMES)} entries '
            f'but the scratch table only holds {FORCE_BOT_AMMO_MAX}'
        )
    encoded = []
    for i, value in enumerate(FORCE_BOT_AMMO_NAMES):
        data = _validate_bot_item_name(value, f'FORCE_BOT_AMMO_NAMES[{i}]')
        if len(data) > FORCE_BOT_AMMO_SLOT_SIZE:
            raise ValueError(
                f'FORCE_BOT_AMMO_NAMES[{i}]={value!r} is too long for a '
                f'{FORCE_BOT_AMMO_SLOT_SIZE}-byte slot'
            )
        encoded.append(data)
    return encoded


def resolve_force_bot_item_name():
    """Return the configured forced bot item name as NUL-terminated ASCII."""
    if FORCE_BOT_ITEM_NAME is not None:
        return _validate_bot_item_name(FORCE_BOT_ITEM_NAME, 'FORCE_BOT_ITEM_NAME')

    if DEBUG_BOT_WEAPON_INDEX is None:
        return None

    if type(DEBUG_BOT_WEAPON_INDEX) is not int:
        raise ValueError(
            'DEBUG_BOT_WEAPON_INDEX must be an integer index or None, '
            f'got {DEBUG_BOT_WEAPON_INDEX!r}'
        )
    if DEBUG_BOT_WEAPON_INDEX < 0 or DEBUG_BOT_WEAPON_INDEX >= len(DEBUG_BOT_WEAPON_NAMES):
        raise ValueError(
            'DEBUG_BOT_WEAPON_INDEX is out of range for DEBUG_BOT_WEAPON_NAMES '
            f'({DEBUG_BOT_WEAPON_INDEX!r} for {len(DEBUG_BOT_WEAPON_NAMES)} entries)'
        )

    return _validate_bot_item_name(
        DEBUG_BOT_WEAPON_NAMES[DEBUG_BOT_WEAPON_INDEX],
        f'DEBUG_BOT_WEAPON_NAMES[{DEBUG_BOT_WEAPON_INDEX}]',
    )

# --- Mode-detection override --------------------------------------------
# Auto-detection (reading [mpd+0] as a vtable) is currently unreliable: mpd
# is the polymorphic CMultiPlayerGameData base, and its vtable is shared
# across DM/CTF/SK. Until the real game-type lookup is found, set this to
# 'dm', 'ctf', or 'sk' to force detect_mode to return that mode regardless
# of what mpd holds. Use None for auto-detect (currently DM-biased).
FORCE_MODE = None  # one of: None | 'dm' | 'ctf' | 'sk'

# --- Synthetic DirectPlay id range (Phase B queue injection) -------------
# Every bot is assigned a unique id from this contiguous range so the
# name-block detour can recognize bot participants by id alone.
SYNTHETIC_ID_LO = 0xBADC0DE0
SYNTHETIC_ID_HI = 0xBADC0DF0  # exclusive upper bound
MAX_BOT_SLOTS   = SYNTHETIC_ID_HI - SYNTHETIC_ID_LO

# --- Bot display names ---------------------------------------------------
# Each ASCII name is encoded as a null-terminated wide string (UTF-16LE) and
# packed into a fixed slot (NAME_SLOT_SIZE bytes) in the .zaxbot scratch
# table. Keep ASCII chars only and length <= 14 (engine's MultiByteStr
# buffer is 15 bytes including the null terminator). Edit freely — names
# are picked uniformly at random per spawn.
BOT_NAMES = [
    "Crusher", "Ripper", "Predator", "Stalker", "Reaper",
    "Hunter", "Phantom", "Shade", "Wraith", "Goliath",
    "Titan", "Apex", "Specter", "Howler", "Talon",
    "Brutus", "Maverick", "Fang", "Drone-X", "Nightmare",
]
NUM_BOT_NAMES   = len(BOT_NAMES)
NAME_SLOT_SIZE  = 32   # bytes per wide-char name slot (14 wide chars + null)
NAME_SLOT_ASCII = 16   # bytes per ASCII name slot (used by sub_4E1930 path)

# --- Per-name colors -----------------------------------------------------
# Each bot name owns a deterministic (color1, color2) pair so every "Ripper"
# looks identical across spawns and sessions. Values are slider units in the
# engine's 0..315 range (`sub_4101F0(a1, 315, 1)` in `sub_46D010`). CTF will
# preempt color1 with the team palette at render time — that's accepted.
COLOR_SLIDER_MAX = 315


def _color_pair(name):
    c1 = sum(ord(ch) * 31 for ch in name) % (COLOR_SLIDER_MAX + 1)
    c2 = sum(ord(ch) * (i + 1) * 37 for i, ch in enumerate(name)) % (COLOR_SLIDER_MAX + 1)
    return c1, c2


BOT_COLORS = [_color_pair(n) for n in BOT_NAMES]  # parallel to BOT_NAMES


# --- Waypoint overlay (visualization for waypoint authoring) -------------
# When True, the page-flip detour at sub_5693A0 draws OVERLAY_WAYPOINTS as
# circles and OVERLAY_EDGES as line segments on top of the rendered frame.
# Coordinates are WORLD-space — the engine's renderer applies the camera
# transform internally (vtbl[+0xAC]/[+0xB0] in CGraphics, off_5FF360), so
# vertices stay glued to the right map position as the camera scrolls.
#
# Install the page-flip hook needed for the visual waypoint overlay and the
# runtime O-key draw toggle. The hook fast-skips when overlay_enabled is 0.
OVERLAY_HOOK_ENABLED = True

# Initial draw state. Keep this False for normal FPS: the N/J/X editor and
# saved graph following still work, and O toggles the visual graph in-game
# only when authoring. Drawing every vertex/edge every frame is expensive on
# Windows 11 when large graphs are visible.
OVERLAY_ENABLED = False
OVERLAY_WAYPOINTS = [(100.0, 200.0), (300.0, 200.0), (300.0, 400.0), (100.0, 400.0)]  # type: list[tuple[float, float]]
OVERLAY_EDGES     = [(0, 1), (1, 2), (2, 3), (3, 0)]  # type: list[tuple[int, int]]

# Capacity ceilings packed into the scratch layout. Bumping them grows
# the .zaxbot section but doesn't slow rendering — the runtime loops on
# the live count fields. Each vertex is 8 B (two floats); each edge is
# 4 B (two u16 indices).
OVERLAY_VERTEX_MAX = 256
OVERLAY_EDGE_MAX   = 512

# Vertex / edge styling. RGBA bytes (0..255) get baked into a 16-byte
# CColor struct via sub_53F010 each frame so the palette index stays
# valid in 8-bit display modes.
# IMPORTANT — overlay colors are effectively BLUE-CHANNEL ONLY in the game's
# 8-bit palettized display mode (historically observed under Wine). sub_53F010 stamps each
# CColor's palette index via sub_433A10(BLUE) — derived from the blue byte alone
# — and the line drawer (sub_568D90) uses that palette index, NOT the RGB. So the
# rendered color depends only on blue: blue=0 => palette index 0 => BLACK
# (red/green are ignored); blue=255 => a visible bright color. Confirmed in-game
# (2026-06-01): colors with B=0 rendered BLACK in 8-bit mode. Keep visible
# graph elements on non-zero blue values so the authoring overlay is actually
# visible. The RGBA tuples below are kept as human labels; only blue actually
# drives the hue in palettized mode.
OVERLAY_VERTEX_COLOR   = (255, 255, 255, 255) # white; B=255 -> visible in 8-bit mode
OVERLAY_EDGE_COLOR     = (64, 160, 255, 255)  # blue/cyan; B=255 -> visible in 8-bit mode
OVERLAY_SELECTED_COLOR = (255, 0, 255, 255)   # magenta; B=255 -> visible selected node
OVERLAY_PICKUP_COLOR   = (0, 255, 255, 255)   # cyan; B=255 -> visible detected pickups
OVERLAY_VERTEX_RADIUS  = 8.0                  # world-space pixels
OVERLAY_VERTEX_ASPECT  = 1.0                  # y/x ratio (1.0 = round)

# Cheap screen-space cull before calling the expensive engine line/oval
# helpers. Zax.CFG currently runs at 640x480. The margin keeps near-edge
# nodes and lines visible while skipping most of the off-screen authored graph.
OVERLAY_CULL_MARGIN = 96.0
OVERLAY_CULL_MIN_X  = -OVERLAY_CULL_MARGIN
OVERLAY_CULL_MIN_Y  = -OVERLAY_CULL_MARGIN
OVERLAY_CULL_MAX_X  = 640.0 + OVERLAY_CULL_MARGIN
OVERLAY_CULL_MAX_Y  = 480.0 + OVERLAY_CULL_MARGIN

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
# Max pickups tracked per frame (each slot is 8 bytes: x, y floats). Greed
# maps scatter many ore / ammo / health / energy items; size generously.
PICKUP_TABLE_MAX        = 96
# Only register pickups that are CURRENTLY collectible. Respawning spawners
# keep ticking sub_53DA40 after the item is taken (item hidden, waiting to
# respawn), so without this their markers/targets would persist on an empty
# pad. The engine marks an item present by setting bits 0x40000|0x20000 in the
# entity flags at +0x1C (sub_53DA40's respawn path sets them; collection clears
# them); these are general "visible/active" flags, so dropped items on the
# ground carry them too and still pass (and when collected their entity is
# destroyed, so they drop out regardless). Register only when
# (flags & PICKUP_ACTIVE_MASK) == PICKUP_ACTIVE_VALUE. Set MASK = 0 to disable
# the filter and register every ticking pickup (debug / fallback).
PICKUP_ACTIVE_MASK      = 0x60000
PICKUP_ACTIVE_VALUE     = 0x60000

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


def resolve_overlay_data():
    """Validate cfg.OVERLAY_WAYPOINTS and OVERLAY_EDGES; return packed lists.

    Raises ValueError on out-of-range edge indices or capacity overflow.
    Empty lists are valid — the runtime renders nothing in that case.
    """
    waypoints = list(OVERLAY_WAYPOINTS)
    if len(waypoints) > OVERLAY_VERTEX_MAX:
        raise ValueError(
            f'OVERLAY_WAYPOINTS has {len(waypoints)} entries; max '
            f'{OVERLAY_VERTEX_MAX} (raise OVERLAY_VERTEX_MAX)'
        )
    for i, p in enumerate(waypoints):
        if type(p) is not tuple or len(p) != 2:
            raise ValueError(f'OVERLAY_WAYPOINTS[{i}] must be (x, y); got {p!r}')

    edges = list(OVERLAY_EDGES)
    if len(edges) > OVERLAY_EDGE_MAX:
        raise ValueError(
            f'OVERLAY_EDGES has {len(edges)} entries; max '
            f'{OVERLAY_EDGE_MAX} (raise OVERLAY_EDGE_MAX)'
        )
    for k, e in enumerate(edges):
        if type(e) is not tuple or len(e) != 2:
            raise ValueError(f'OVERLAY_EDGES[{k}] must be (i, j); got {e!r}')
        i, j = e
        if not (0 <= i < len(waypoints)) or not (0 <= j < len(waypoints)):
            raise ValueError(
                f'OVERLAY_EDGES[{k}]=({i},{j}) out of range for '
                f'{len(waypoints)} vertices'
            )
        if i > 0xFFFF or j > 0xFFFF:
            raise ValueError(f'OVERLAY_EDGES[{k}] index >0xFFFF not packable as u16')
    return waypoints, edges
