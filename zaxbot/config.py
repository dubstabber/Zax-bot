"""Bot-policy configuration: section layout, scratch sizes, bot names, and the
synthetic DirectPlay id range used by Phase B's queue-injection spawn flow.

These are all knobs that can change without re-reverse-engineering the engine
(unlike `addresses.py`). Keep raw engine VAs out of this module.
"""

from .build import SectionSpec


# --- new section parameters (.zaxbot) -------------------------------------
NEW_SECTION_NAME   = b'.zaxbot\x00'
NEW_SECTION_VA     = 0x31A000      # RVA; absolute = 0x71A000
NEW_SECTION_SIZE   = 0x26000       # 40KB code + 112KB scratch (grown for the door detection
                                   # tables, the door-aware routing field, its per-team
                                   # split, the switch detection tables, the portal
                                   # routing layer — dest tables + node bindings — the
                                   # dropped-flag pursuit layer: drop_dist BFS rows —
                                   # then the SK layer: 1856 static mineral anchors,
                                   # per-team bin tables, the mineral field + 16 bin
                                   # BFS rows, and the 512-slot pickup table)
SECTION_CHARACTERS = 0xE0000020    # CODE | EXEC | READ | WRITE
HOOK_ENTRY_OFF     = 0x000
SCRATCH_OFF        = 0xA000        # writable scratch buffer; 40KB code / 112KB scratch
                                   # (boundary moved from 0x5A00 at the door layer, from
                                   # 0x6800 at the switch layer, from 0x7000 at the
                                   # portal-routing layer, from 0x8000 when the
                                   # dropped-flag ROUTED pursuit landed with ~456 code
                                   # bytes left, then from 0x9000 at the SK layer with
                                   # ~3.0KB code left)

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

# Conditional "good enough" arrival for wedged bots. Normal movement still uses
# WP_REACHED_RADIUS_SQ so tight graph corners are not skipped early. But if the
# bot is already visibly stuck or not making waypoint progress and is within
# this larger radius, accept the node and advance. The latest R-dump showed a
# far CTF bot pinned ~75px from its target node with the normal 64px radius just
# out of reach; previous dumps had the same pattern at ~100px.
WP_STUCK_REACHED_RADIUS_SQ = 16384.0

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
# failed target, the detour preserves the stall state and lets the angle sweep
# continue instead of resetting the escape attempt every timeout window.
# Edge-following while genuinely progressing is untouched.
# ~2.5s at 60Hz. Lower = recovers faster but may interrupt legitimately-slow
# progress (squeezing past the host); higher = visible pin before recovery.
WP_PROGRESS_TIMEOUT_FRAMES = 30

# Historical knob for the removed random-wander relocate burst. The scratch slot
# is now repurposed for WP_STUCK_REACHED_RADIUS_SQ to avoid shifting the runtime
# layout; the name is kept only for compatibility with older notes/tests.
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
OVERLAY_PORTAL_COLOR   = (255, 64, 255, 255)  # pink; B=255 -> visible detected teleports
OVERLAY_FLAG_COLOR     = (0, 0, 255, 255)     # blue; B=255 -> visible detected CTF flags
# Doors render with the same palette index as every other B=255 marker in the
# 8-bit mode (see the color quirk above) — a CLOSED door is distinguished by a
# second, double-radius ring drawn around its marker, an OPEN door by a single
# small oval. Distinguish door vs flag/portal points by position/count.
OVERLAY_DOOR_COLOR     = (255, 128, 255, 255) # B=255 -> visible detected doors
OVERLAY_SWITCH_COLOR   = (128, 255, 255, 255) # B=255 -> visible detected switches
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
# Max pickups tracked per frame (each slot is 8 bytes: x, y floats). Sized to
# the Data.dat census (2026-07-19): SK mode loads every mineral, and The
# Foundry peaks at 502 total pickups (386 minerals + the weapon/ammo/health
# mix); EVERY SK map exceeds the old 96 cap (min 135), which was the reported
# "only ~80-90% of ores are marked" overlay gap — pickups past the cap simply
# never registered. Non-SK modes stay well under (max: Battle on the Ice 74).
PICKUP_TABLE_MAX        = 512

# --- Teleport/portal detection -------------------------------------------
# Portal source trigger centers are extracted from Data.dat at patch-build time
# by parsing .zax map text records. Runtime code only compares the active map
# name against this compact static table and copies the matching points into
# portal_table on match change, so no heap-wide scanning happens in-game.
PORTAL_TABLE_MAX        = 32  # live overlay points, float[2] each
PORTAL_STATIC_MAP_MAX   = 8   # shipped Data.dat currently has 4 portal maps
PORTAL_STATIC_POINT_MAX = 32  # shipped Data.dat currently has 10 portal points
PORTAL_MAP_NAME_SLOT    = 96  # fixed ASCII bytes per map path, including NUL

# --- Teleport/portal ROUTING (bots use portals as directed graph edges) ----
# When a portal's build-time parse also resolves its teleport DESTINATION
# (Data.dat `New Location` -> a positioned Level Part, e.g. Hydro Vengence's
# four warm/cold pads), the portal becomes a DIRECTED edge for CTF routing:
# bind_portal_nodes (per match, after wp_load + load_portals) binds the source
# pad and the destination point each to their nearest graph node, and bfs_run
# relaxes source_node -> dest_node in every distance field it fills (full,
# per-team open, switch-seek). On Hydro Vengence the two arenas are only
# connected through the pads, so without this the enemy base is BFS-
# unreachable and CTF bots can neither steal nor return flags. At node
# arrival, when the pad at the current node is the shortest next hop,
# ctf_next_hop reports a portal hop instead of a neighbour node and the
# follower latches a PAD FINAL-APPROACH: steer at the pad center (same
# watchdog as the flag final approach — no progress ramps the wall-slide,
# a full timeout clears the latch and suspends routing). The teleport itself
# is detected position-side: any per-think jump farther than
# sqrt(PORTAL_JUMP_REACQUIRE_DIST_SQ) drops the whole nav latch and
# cold-acquires the NEAREST node at the arrival point — this also covers
# bots knocked through script teleporters they never chose. Portal fields
# are NOT rebuilt when a pad's active state flips mid-match (only door
# changes rebuild); a route through a currently-inactive pad ends in the
# standard blocked-route machinery (watchdog -> suspension -> roam), and the
# live portal_active[] gate below keeps the next-hop/latch itself honest.
PORTAL_ROUTING_ENABLED = True
# Roaming bots (DM matches, and CTF bots whose routing is suspended or whose
# goal is missing) occasionally step INTO an adjacent active pad: at each
# node arrival that falls back to the random neighbour pick, if the current
# node is some active pad's nearest node, roll RNG(0..99) < this and latch
# the pad approach instead of a neighbour. 0 disables. This is the whole
# "teleports are part of the wander space" behaviour asked for DM maps —
# no destination knowledge needed (the jump re-acquire recovers the graph).
PORTAL_WANDER_CHANCE = 25
# The wander-entry roll is SKIPPED while the bot's routing is suspended
# (suspension roam is a LOCAL unstick — live snapshots caught a suspended
# CARRIER bouncing arena-to-arena on the roll) and for this many thinks after
# any teleport (each pad's exit node IS the return pad's node, so the very
# next arrival would re-roll the coin — the observed pad ping-pong). Routed
# pad hops are unaffected (they only fire on strictly-descending distance).
PORTAL_WANDER_COOLDOWN_FRAMES = 600
# Pad-press patience (mirror of WP_DOOR_PRESS_PATIENCE): a pad final-approach
# whose progress watchdog times out gets this many fresh watchdog cycles —
# the wall-slide sweep keeps hunting for the thin trigger sliver around the
# collidable teleporter prop — before the latch is dropped and routing
# suspends. 3 cycles ≈ 2 s of pressing at 60 Hz.
PORTAL_PRESS_PATIENCE = 3
# Post-teleport RETURN-PAD heading veto (the anti-ping-pong virtual wall).
# Live proute snapshots pinned the residual bounce loop: the teleport drops
# the bot at the exit marker inside a collision pocket around the teleporter
# prop, ~28 px from the RETURN pad's thin trigger sliver; the wall-slide
# sweep rotates the blocked heading and the first direction that actually
# moves the bot walks it across that sliver — an ENGINE re-teleport, no bot
# decision involved, so the wander gates could not stop it. While the
# post-teleport cooldown runs, the emitted heading is vetoed (rotated on,
# lava-veto style) whenever its lookahead point lands within sqrt(this) of
# any pad center the bot has NOT deliberately latched (the latched pad must
# stay enterable — returning through a pad is often the correct route).
# 40 px rejects every sliver-ward heading from the exit pocket while leaving
# the directly-away and along-the-ledge escapes open; the lookahead distance
# reuses LAVA_LOOKAHEAD_PX and the sweep step LAVA_SWEEP_STEP_DEG.
PORTAL_VETO_RADIUS_SQ = 40.0 * 40.0
# A bot that moves farther than sqrt(this) between two consecutive movement
# thinks has been teleported (engine step is ~1.7 px/frame; knockback stays
# well under 100 px). Fires the post-teleport nearest-node re-acquire and
# clears any pad latch. Keep far above real per-frame movement and far below
# the shortest shipped teleport span (Hydro pads jump ~1600 px).
PORTAL_JUMP_REACQUIRE_DIST_SQ = 192.0 * 192.0

# --- CTF flag detection --------------------------------------------------
# CTF flag home positions are extracted from Data.dat at patch-build time by
# parsing each multiplayer .zax map for its "Red Flag Spawn" / "Blue Flag
# Spawn" Level Parts (the flag base anchors). Runtime code only compares the
# active map name against this compact static table and copies the matching
# points into flag_table on match change — identical to the portal pipeline,
# no heap-wide scanning in-game.
#
# flag_present[] ("is that team's flag home?") is EVENT-DRIVEN, mirroring the
# vanilla rule exactly. Every CTF map authors a hidden "Red Checker" / "Blue
# Checker" touch trigger exactly on the flag spawn anchor; the shared canned
# scripts (Data.dat "Picked up a Flag" / "Returned a Flag") deactivate that
# checker when the team's flag is stolen and reactivate it when the flag is
# returned or reset after a capture — a deactivated checker never fires its
# capture action, which IS the vanilla "own flag must be home" enforcement.
# The patch detours the CActivateAction/CDeactivateAction per-entity applies
# (sub_4C29F0 / sub_4C2D60) and, when the resolved target entity sits on a
# flag_table anchor (it is the checker), writes flag_present[] = 1/0. Flags
# start home (load_flags seeds 1) and there is no engine-side auto-return, so
# the two script events are the complete transition set. Heuristics that
# previously derived presence from anchor-entity counts, carried-inventory
# scans and dropped-item grid matches were removed: the world flag entity is a
# plain unnamed-or-renamed CEntityAnimated with no inventory identity, so
# those scans could not see a dropped flag and left flag_present stuck at 1.
FLAG_TABLE_MAX        = 8   # live overlay points, float[2] each (2 flags/map)
FLAG_STATIC_MAP_MAX   = 8   # shipped Data.dat currently has 7 flag maps
FLAG_STATIC_POINT_MAX = 16  # shipped Data.dat currently has 14 flag points
FLAG_MAP_NAME_SLOT    = 96  # fixed ASCII bytes per map path, including NUL
# Exact-anchor entity cache used by the far-base force-tick. Up to three
# distinct entities can legitimately sit on the anchor (checker trigger, spawn
# position marker, and the flag entity itself once a return/capture recreated
# it exactly at the spawn); two slots could evict the checker depending on
# grid iteration order, which silently broke far captures.
FLAG_ENTITY_SLOTS_PER_FLAG = 3
CTF_FLAG_ENTITY_MATCH_RADIUS_SQ = 16.0 * 16.0
CTF_FLAG_HOME_FORCE_TICK_RADIUS_SQ = 64.0 * 64.0
# Master gate for the CActivateAction/CDeactivateAction apply detours that
# keep flag_present[] in lockstep with the map script's checker state.
CTF_FLAG_EVENTS_ENABLED = True

# --- Door detection --------------------------------------------------------
# Door positions are extracted from Data.dat at patch-build time by parsing
# each multiplayer .zax map for Level Parts carrying Activity=CDoorAI (see
# zaxbot/door_data.py and the door-runtime-model notes). load_doors copies the
# active map's door centers into door_table on match change — identical to the
# portal/flag pipelines, no heap-wide scanning in-game.
#
# Door STATE is PER-FRAME fresh. The periodic grid walk (scan_portal_active,
# every PORTAL_ACTIVE_SCAN_INTERVAL frames) only maintains a per-anchor entity
# CACHE (door_entity[], up to DOOR_ENTITY_SLOTS_PER_DOOR non-character
# entities within sqrt(DOOR_ENTITY_MATCH_RADIUS_SQ) of each door anchor); the
# page-flip hook then re-reads the cached entities' SOLID flag
# (entity+0x1C & 0x40000 — set while the door is closed, cleared by the open
# path) EVERY frame into door_blocked[]. Deriving state from the walk itself
# was live-tested and rejected: the walk interval is counted in FRAMES, so
# with the overlay visible (low FPS) 120 frames stretched to many seconds and
# the door rings looked permanently stale (toggling the overlay off restored
# FPS, let a scan through, and "fixed" it). Trigger pads / markers cached at
# the same anchor are non-solid so they never false-positive; live player
# characters are excluded from the cache exactly like the CTF flag anchor
# cache (a bot standing in an open doorway is SOLID but is not a door).
# Consumers:
# - failed-edge fast retry: a marker set while wedged against a blocked door
#   clears the moment that door reads passable again, instead of waiting out
#   the blind WP_ROUTE_BLOCK_RETRY_HITS cadence.
# - door-aware CTF rerouting (DOOR_ROUTE_AWARE_ENABLED below): a second BFS
#   field excludes closed-door edges so bots actively route around them.
# Bots are NOT hard-barred from closed doors — many doors open on approach
# (proximity trigger / touch-open), so the open-field routing always falls
# back to the full field when no door-free path exists.
DOOR_DETECT_ENABLED = True
# Entities cached per door anchor. The door entity itself plus up to two
# co-located authored pieces (arming pad / touch trigger); mirrors the
# FLAG_ENTITY_SLOTS_PER_FLAG eviction rationale.
DOOR_ENTITY_SLOTS_PER_DOOR = 3
# Live per-map door table. Curse of the Temple authors 186 CDoorAI doors —
# the largest shipped MP count — so the cap must sit above that.
DOOR_TABLE_MAX        = 192
DOOR_STATIC_MAP_MAX   = 12   # shipped Data.dat has 10 MP door maps
DOOR_STATIC_POINT_MAX = 384  # shipped Data.dat has 333 MP door points
DOOR_MAP_NAME_SLOT    = 96   # fixed ASCII bytes per map path, including NUL
# An entity "sits on" a door anchor when within sqrt() of it. The authored
# Level Part position IS the entity's raw +0x4C/+0x50, so this only needs to
# absorb float noise — keep it tight so nearby genuinely-solid scenery can't
# claim the anchor.
DOOR_ENTITY_MATCH_RADIUS_SQ = 24.0 * 24.0
# When the progress watchdog marks a failed edge, the nearest currently-
# blocked door within sqrt() of the BOT (it is physically pressed against the
# obstacle at that moment) is recorded alongside the marker. Generous enough
# to cover door half-width + bot collision radius; a wrong latch is self-
# correcting (marker just falls back to the blind retry cadence).
DOOR_WEDGE_MATCH_RADIUS_SQ = 96.0 * 96.0

# --- Door-aware CTF rerouting ----------------------------------------------
# The single per-match BFS field always funnels a bot down the SHORTEST path,
# so a bot pinned at closed door A never diverted to an alternative corridor
# the moment door B opened (live-reported: two blocked ways to the enemy
# flag; opening the second one did not reroute the bot). With this on,
# build_flag_routes also computes flag_dist_open — the same per-base BFS but
# SKIPPING every graph edge that crosses a currently-blocked door — and
# ctf_next_hop prefers the open field, falling back to the full field
# whenever the goal is unreachable without passing a closed door (so
# approach-openable doors — proximity pads, touch-open — still get walked
# at exactly like before). door_blocked[] changes (per-frame refresh) mark a
# dirty flag; the page-flip hook then rebuilds ONLY the open field, debounced
# by DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES (touch-open-door maps flip state
# constantly; the BFS is integer-cheap but there is no reason to run it every
# frame). Edge->door adjacency is STATIC per match (doors and the graph don't
# move): build_edge_doors records, per graph edge, the nearest door within
# sqrt(DOOR_EDGE_RADIUS_SQ) of the edge SEGMENT (point-segment distance).
DOOR_ROUTE_AWARE_ENABLED = True
DOOR_EDGE_RADIUS_SQ = 40.0 * 40.0
DOOR_ROUTE_REBUILD_COOLDOWN_FRAMES = 30
# Directional passability. A CLOSED door edge is traversable from side S iff
# a bot-usable opener (walk-in trigger — touching/pass-through volumes the
# bot fires just by moving; NOT collide switches / spawn triggers / relays /
# timers) sits on side S of the door, where side is the sign of
# dot(opener - door, node_S - door) with a +1.0 bias so an opener exactly ON
# the door (self-trigger walk-up doors) grants BOTH sides. Doors with no
# authored opener of any kind are engine bump-open => both sides. Doors with
# only non-bot-usable openers are impassable while closed — live state flips
# them the moment something opens them (spawn doors, switch doors, timer
# jaws). Team-gated self-trigger doors (Doom ship) are treated optimistically
# as openable — a wrong-team bot falls back to the wedge machinery.
# Openers per map are small (Curse of the Temple peaks at ~22); the static
# table holds every MP map's bindings (53 shipped). Each record is
# (x f32, y f32, door_idx u32, team_mask u32) — the mask restricts same-team-
# conditional walk-up doors (Doom ship / Battle on the Ice / Curse) to their
# own team; unconditional openers carry mask 3.
DOOR_OPENER_TABLE_MAX  = 48   # live per-map opener records
DOOR_OPENER_STATIC_MAX = 96   # build-time records across all MP maps
# PHYSICAL-STATE routing override. When True, the open-field BFS and
# ctf_next_hop treat EVERY currently-closed door as impassable and route
# AROUND it (using the live door_blocked[] state), ignoring the edge_pass
# team-openability bits above. Rationale: a bot far from the host's camera
# cannot open ANY door — the touch/switch triggers are camera-gated and never
# fire — so routing a carrier THROUGH a team-openable-but-closed door stranded
# it pressing a door that never opens until the host approached (live-reported
# on Battle on the Ice: a team-1 carrier committed to the openable door on its
# way home instead of the 12-hop door-free path). Routing around closed doors
# and only USING them once they read open (the epoch reroute picks them up the
# frame they flip) is robust regardless of camera distance and still honours
# team gating (a closed enemy-team door is avoided either way). Set False to
# restore the directional edge_pass behaviour (route through doors your team
# could open) — only worthwhile once bots can trigger far doors themselves.
DOOR_ROUTE_PHYSICAL_STATE = False

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

# --- CTF flag routing (bots navigate the waypoint graph toward flags) ----
# Master gate. When on and the active match is CTF with a graph + flags, bots
# route through the authored waypoint graph toward a flag base instead of
# roaming randomly: NOT carrying -> head to the ENEMY base; carrying the enemy
# flag -> head to OWN base to capture. The path is a true shortest path — a
# per-base BFS hop-distance field (flag_dist) is precomputed once per match at
# load (build_flag_routes, from detour_df90); at each node arrival the follower
# steps to the neighbour with the smallest distance to the goal base (strictly
# decreasing => guaranteed progress). Falls back to the random neighbour pick
# (wp_advance) whenever routing can't apply (non-CTF, no graph, no flags, goal
# unreachable from here). If an attacker sees the enemy flag absent from its
# base, the bot rolls a stable temporary policy for that missing-flag episode:
# search by random waypoint roaming, or keep routing toward the missing flag's
# base to wait/patrol nearby. If a carrier's OWN flag is absent from home, it
# always searches instead; the far-base force-tick is also gated on
# flag_present[home] so bots cannot score at an empty home base. Routing to the
# live dropped-flag position remains future work. See ctf-flag-detection,
# ctf-flag-carry-detection.
CTF_FLAG_ROUTING_ENABLED = True
# --- CTF dropped-flag pursuit ---------------------------------------------
# When a flag is away from its base (flag_present[i] == 0) the periodic grid
# walk (scan_portal_active cadence) also looks for the DROPPED world copy —
# the script-created CEntityAnimated the drop-on-death canned script names
# exactly "Red Flag" / "Blue Flag" (Data.dat "Does player have a flag";
# entity name read via [ent+0x18]+8, the sub_4FBF20 CString chain) — and
# records its position in flag_drop_pos[] / flag_drop_valid[] and binds its
# nearest graph node into flag_drop_node[] (drop_route_refresh then fills a
# per-drop BFS hop field, drop_dist, rooted at that node). Pursuit is
# TWO-PHASE (v2 — the v1 straight-steer-only pursuit was live-diagnosed
# giving up after one 30-frame watchdog window and cooling down 4 s, the
# "runs at it, then ignores it" report; and it beelined into walls):
#   * ROUTED: a latched bot beyond the direct radius descends drop_dist at
#     every node arrival (drop_next_hop overrides ctf_next_hop while latched
#     and not suspended), so walls are routed AROUND via the graph.
#   * DIRECT: within CTF_DROP_DIRECT_RADIUS_SQ — or standing on the drop's
#     own graph node — steer straight at the copy through the standard
#     watchdog, with CTF_DROP_PRESS_PATIENCE fresh cycles before giving up.
# LATCHING: any bot within CTF_DROP_PURSUE_RADIUS_SQ opportunistically
# diverts; a bot whose GOAL flag is the missing one (route_missing_goal —
# attackers whose steal target is dropped, carriers whose home flag is
# dropped) latches from ANY distance, replacing the blind search/wait roam
# with a route to where the flag actually lies. Touching a dropped flag is
# beneficial for EITHER team (same team returns it home, the enemy picks it
# up), so there is no team filter. The name match is exact and gated on
# flag_present[i]==0, which also excludes the 7 authored at-base blue-flag
# icons that carry the same name — they are consumed the moment the flag is
# stolen, so no world entity collides with the name while a flag is away
# (census pinned in tests). Stale-position windows are bounded by one scan
# interval (PORTAL_ACTIVE_SCAN_INTERVAL).
CTF_DROPPED_FLAG_ENABLED = True
# Opportunistic divert radius (squared px): a bot passing within sqrt(this)
# of a drop takes it even when its own goal lies elsewhere.
CTF_DROP_PURSUE_RADIUS_SQ = 350.0 * 350.0
# The divert ends (touch assumed) within sqrt(this) — mirror of
# PICKUP_REACHED_RADIUS_SQ: the flag's own PassThrough/touch script consumes
# the copy on overlap, so the bot only needs to overlap it.
CTF_DROP_REACHED_RADIUS_SQ = 24.0 * 24.0
# Straight-steer phase radius (squared px). Beyond it the bot node-routes via
# drop_dist; within it (or at the drop's own bound node, where the graph can
# take it no closer) it walks straight at the copy. Keep near the waypoint
# spacing scale — the v1 failure was straight-steering over 250+ px of
# geometry.
CTF_DROP_DIRECT_RADIUS_SQ = 160.0 * 160.0
# A latched OPPORTUNISTIC pursuit is silently dropped beyond sqrt(this)
# (knockback, detour drift). Objective bots (route_missing_goal == the drop)
# are exempt — they route from anywhere.
CTF_DROP_ABANDON_RADIUS_SQ = 700.0 * 700.0
# Direct-phase press patience: a progress-timeout grants this many fresh
# watchdog cycles (wall-slide keeps sweeping) before the retry cooldown.
# Live snapshots caught a drop lying in a collision pocket where the bot's
# closest approach was ~47 px across two cycles — a third cycle buys one
# more full sweep before the 4 s blacklist (rare-awkward-drop mitigation).
CTF_DROP_PRESS_PATIENCE = 3
# After ENDING a pursuit by reaching the spot, don't re-latch for this many
# thinks. MUST exceed PORTAL_ACTIVE_SCAN_INTERVAL: the consumed copy's stale
# position survives in flag_drop_valid until the next scan clears it, and a
# shorter cooldown would re-latch the bot onto the ghost.
CTF_DROP_GRAB_COOLDOWN_FRAMES = 150
# After direct-phase patience is exhausted (drop nearby but physically
# unreachable even with the slide sweep), blacklist pursuing for this many
# thinks so the bot resumes the graph. It retries automatically afterwards
# if the flag still lies there.
CTF_DROP_RETRY_COOLDOWN_FRAMES = 240
# Guard the engine score action itself: a map-script capture point award is
# suppressed when the scoring team's own flag is away from base or carried by a
# player. Last-resort backstop behind the event-driven flag_present[] — with
# checker wake-ups gated correctly this should never fire. The old companion
# guard on CUseInventoryItemAction was REMOVED: the drop-on-death canned script
# consumes the dying carrier's flag through the same action, so that guard
# wrongly blocked flag drops whenever both flags were out.
CTF_SCORE_GUARD_ENABLED = True
# Flag bases the BFS distance field is precomputed for (CTF always has 2).
# flag_dist costs FLAG_ROUTE_MAX * OVERLAY_VERTEX_MAX dwords of scratch.
FLAG_ROUTE_MAX        = 2
# Per-bot routing suspension. BFS routing is deterministic, so a bot whose
# shortest path is physically blocked (closed door the camera-gated engine
# never opens, geometry pinch the slide can't clear) would be funnelled back
# into the same blocked segment forever — visible as a carrier pinned at
# "certain waypoints" until the goal changes. After a routed progress-timeout
# (including the CTF final approach to the flag itself), the bot gives up
# routing for this many frames and roams the graph randomly (the same
# behavior that visibly un-sticks it when the flag state changes), then
# routing resumes automatically.
WP_ROUTE_SUSPEND_FRAMES = 240
# Wedge-cluster HARD RESET (live-diagnosed 2026-07-20, Battle on the Ice R
# snaps 1-3): a bot on the WRONG SIDE of a wall/door whose latched nodes sit
# across it cycles the local recovery forever — the alternate-neighbour path
# only explores neighbours of prev (all across the wall), retreat swaps within
# the same pair, and the unlatched reacquire re-picks the Euclidean-nearest
# node (also across the wall; live: cur flipped 77<->47 with prev=78 while the
# bot stood north of the closed south door, and the reachable around-route via
# node 48 was never tried). After this many consecutive recovery actions
# WITHOUT a single node arrival, the follower cold-acquires the nearest node
# EXCLUDING the wedge cluster (failed cur, prev, and the failed-edge marker's
# two nodes) via wp_find_nearest_ex — on the live geometry that excludes
# {47,77,78} and picks 48, the entry to the around-route. The marker is KEPT
# through the reset as wedge memory. Any genuine arrival resets the counter.
# Must stay <= 127 (imm8).
WP_WEDGE_RESET_CYCLES = 3
# FIGHT-STALL suppression (user-reported 2026-07-20: "bots that hold the flag
# do not always escape to the base when engaged in fighting"): a routed
# progress stall with a live enemy this close (d^2, px^2) is usually the fight
# itself — knockback and body-blocking — not geometry. Arming the routing
# suspension there made ctf_pick_goal report no goal, so a flag CARRIER
# roamed randomly mid-fight instead of pressing home. While the fire scan's
# per-bot enemy-near stamp is set, the progress-timeout skips the suspension
# (markers, alternates and the wedge hard reset still run). 240 px ~ just
# beyond melee/body-block range; raise toward FIRE_RANGE if carriers still
# loiter in longer-range duels.
FIGHT_STALL_RADIUS_SQ = 240.0 * 240.0
# Physical-length routing quantum: every graph edge's traversal cost in the
# shared BFS (bfs_run — full/open/seek/drop fields alike) is its pixel length
# divided by this, rounded, min 1. Hop counting was live-refuted on Hydroplant
# Bouncefest: the through-door route and the around-the-top route are both 9
# hops, so routing (and the seek benefit gate) saw zero gain from opening the
# switch-doors even though the door route is 681 px (~26%) shorter. With
# 16 px quanta a 3000 px map maxes out around ~200 units — far from the
# 0xFFFFFFFF unreachable sentinel. Teleport pads keep cost 1 (near-free,
# strongly preferred — matches their old +1 hop).
WP_EDGE_LEN_QUANTUM = 16.0
# The failed-edge marker must be RETRIED, not kept forever. Live CE analysis
# of the reported "carrier stuck near a door" showed the exact loop: the
# marker held the door edge (15,17) long after the door became passable;
# routing wanted 17->15 every arrival, the marker forced the random fallback,
# and node 17's only other neighbour bounced the bot straight back — an
# arrival-level ping-pong with zero timeouts, so the suspension never fired.
# Manually clearing the marker in CE made the bot walk through and capture
# within seconds. After this many consecutive routed hops forced off the
# marked edge, the marker is cleared so the edge is retried: if it is open the
# bot simply passes; if it is still blocked the wedge timeout re-marks it and
# the roam suspension takes over.
WP_ROUTE_BLOCK_RETRY_HITS = 3

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
# After grabbing (or failing) an item divert, no new goody latch for this
# many thinks (~5 s) — also spaces out revisits to a consumed anchor.
ITEM_GRAB_COOLDOWN_FRAMES = 300
# Shared two-phase knobs (piles AND items): straight-steer only within
# sqrt(DIRECT); silently drop a latch whose target drifted beyond
# sqrt(ABANDON) (routed paths legitimately move AWAY from the target around
# walls, so keep this comfortably above the entry radii).
GOODY_DIRECT_RADIUS_SQ  = 160.0 * 160.0
GOODY_ABANDON_RADIUS_SQ = 600.0 * 600.0
# Live per-map filler table (Caves of Gold authors 42 — the shipped max).
ITEM_TABLE_MAX        = 48
ITEM_STATIC_MAP_MAX   = 20   # shipped Data.dat has 17 MP filler maps
ITEM_STATIC_POINT_MAX = 288  # shipped Data.dat has 272 filler anchors
ITEM_MAP_NAME_SLOT    = 96   # fixed ASCII bytes per map path, incl. NUL
ITEM_CATEGORIES       = 3    # health / energy / shield (item_data.ITEM_CAT_*)

# --- Keep bots simulated when far from the host's camera -----------------
# The engine advances an entity's components (incl. the bot walking-controller
# think sub_543B60) only when the char's ACTIVE bit (char+0x1C & 0x800000) is
# set; it DEACTIVATES entities far from the local camera (a one-shot, sticky
# transition — verified live: a cleared bit is NOT re-set per frame). So a bot
# that walks away from the host freezes mid-route (e.g. carrying the flag back
# to its base) until the host approaches. We re-set each live bot char's Active
# bit once per frame from the page-flip hook (cheap 16-slot loop, NOT a
# per-entity hot path), so the engine keeps ticking bots everywhere — in-context
# (no double-tick: the engine still advances each active bot exactly once).
BOT_FORCE_ACTIVE_ENABLED = True
# Setting the Active bit alone is NOT enough: the engine's per-frame update
# DRIVER skips entities far from the local camera entirely. Calling only the
# bot character's component advance (`sub_4FADC0`) is also NOT enough: it reaches
# the walking-controller think, but bypasses the active-entity driver's later
# position sync, so the bot computes movement without changing char+0x4C/+0x50.
# The page-flip hook therefore force-runs the same three per-entity vtable
# stages used by `sub_57A030` (+0x7C, +0x80, +0x8C with EBP=0x10000) for any
# bot the engine skipped this frame. A per-bot bot_ticked flag (0 = skipped,
# 1 = engine ticked, 2 = page-flip recovery tick) prevents double-ticking near
# bots and lets fire/aim suppress stray shots during the recovery tick. Requires
# BOT_FORCE_ACTIVE_ENABLED because those entity stages still gate on Active.
BOT_FORCE_TICK_ENABLED = True
# THE fundamental anti-culling fix: make each bot an engine-native ACTIVATION
# SOURCE, exactly like a real connected player. The MP world update
# (sub_4F37E0, virtual) collects one point per participant — the floats at
# participant+0xC0/+0xC4, on the participant whose layer index at +0xDC is
# valid — and sub_4EA350 turns each point into a screen-sized activation rect;
# sub_4E74A0 then updates every Active entity inside the union of (host
# viewport rect + all participant rects) via the sub_57A100 grid collect. Real
# clients stream their +0xC0 position over DirectPlay; nobody updates a bot's,
# so it stays (0,0) (live-verified) and the world around a far bot is never
# simulated — the root cause of far bots not opening their own team doors, not
# stealing far flags, and freezing mid-route. This flag mirrors each live
# bot's char position (char+0x4C/+0x50) into its participant's +0xC0/+0xC4
# once per frame from the page-flip hook (inside the force-active 16-slot
# loop, so it requires BOT_FORCE_ACTIVE_ENABLED). The engine then simulates
# around bots natively: touch/proximity door triggers think and fire, far
# flag steals/captures work, no force-wake needed. Safe by construction
# against the checker re-arm hazard: sub_57A100 only collects entities whose
# Active bit is SET, so script-deactivated CTF checkers stay asleep — this
# path never touches any entity's Active bit.
BOT_PARTICIPANT_POS_ENABLED = True

# Runtime portal detection: a detour on the relocate/teleport executor
# (sub_4C11A0) self-registers the SOURCE pad of every CTeleportAction warp into
# portal_table the moment any entity teleports — exactly like pickups self-
# register via the CPickupAI update detour. This catches conditional /
# script-driven portals that the static Data.dat parse cannot (those only fire
# once a map condition activates them, e.g. an objective/lock puzzle), so the
# bot learns every active teleporter on any map. The site fires only on an
# actual teleport, never per frame, so it is not a hot path. Build-gated: when
# False the detour code is still emitted (dead) but the patch site is not
# installed. Filters to genuine CTeleportAction warps (ax.VT_TELEPORT_ACTION_VA)
# so plain CRelocateAction "$return"/non-warp moves are ignored.
# --- World entity scanner (detours/entity_scan.py) -----------------------
# scan_entities walks the layer's spatial grid (mgr -> layer -> cells) and
# collects entities matching a class descriptor (0 = every entity) into a
# result table of (ptr, x, y, flags) records. Foundation for object detection
# (switches, doors, CTF flags, SK collectors, traps) and per-portal active
# state (entity flags & ax.ENTITY_ACTIVE_BIT). SCAN_ENTITIES_MAX caps the
# result table (16 bytes/record). A diagnostic pass (scan from detour_df90 on
# match change, gated below) seeds the table so the scanner can be validated
# end-to-end via the result count / R-snapshot before any bot behaviour reads
# it. The walk is bounded (rows*cols cells, 256 entities/cell, both capped) and
# only runs on match change, so it is not a per-frame hot path.
# 128 records (2KB table) comfortably covers a DM/CTF map's placed entities so
# the class=0 diagnostic doesn't truncate before reaching late-cell entities
# (the table-full guard ends the whole walk). A class-filtered scan needs far
# fewer; raise only if a dense map's count approaches this.
SCAN_ENTITIES_MAX     = 128
SCAN_ENTITIES_ENABLED = True

# --- Per-portal active-state (scan_portal_active) ------------------------
# A grid-walk consumer of the entity enumerator that, instead of collecting a
# capped table, matches every entity against portal_table and records the
# NEAREST entity's Active bit into portal_active[i]. Immune to the
# SCAN_ENTITIES_MAX cap (the table is never built), so it reaches the
# teleporter pads wherever they sit in the grid. portal_active[i] is 1 when the
# entity nearest portal_table[i] (within sqrt(radius)) has flags & ENTITY_ACTIVE_
# BIT set, else 0 — i.e. "is this pad currently usable?" (e.g. Jungle Ruins'
# two-lock key puzzle flips it). The pad entity sits ~at the portal centroid, so
# nearest-within-radius reliably picks it; 128px tolerates the source-vs-centroid
# offset (runtime pads landed ~38px off) while staying far under inter-portal
# spacing (~740px), so distinct portals never cross-match.
PORTAL_ACTIVE_ENABLED        = True
PORTAL_ACTIVE_MATCH_RADIUS_SQ = 128.0 * 128.0
# Re-scan cadence (frames) from the page-flip detour so the flag tracks dynamic
# activation/cooldown. The puzzle is solved mid-match, so a match-change-only
# scan would miss it. 120 = ~2s at 60Hz; the walk is bounded, but it is the only
# periodic (not per-frame) cost, so keep it coarse.
PORTAL_ACTIVE_SCAN_INTERVAL = 120
PORTAL_REGISTER_ENABLED = True
# A newly-observed teleport pad within sqrt() of an existing portal_table entry
# (static or runtime) is treated as the same pad and not re-added. The dedup is
# on the SOURCE position, which varies by where on the pad a player stands when
# they trigger it: live testing on Jungle Ruins (DM) showed the same pad
# registering twice from spots ~57px apart with a 48px radius. 128px comfortably
# merges same-pad hits (a pad is at most ~1-2 tiles wide) while staying far below
# typical inter-portal spacing (the two Jungle Ruins pads are ~740px apart), so
# distinct portals never collapse into one. Raise if a large pad still doubles;
# lower if two genuinely-separate nearby portals merge.
PORTAL_DEDUP_RADIUS_SQ  = 128.0 * 128.0
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
