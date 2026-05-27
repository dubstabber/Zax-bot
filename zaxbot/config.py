"""Bot-policy configuration: section layout, scratch sizes, bot names, and the
synthetic DirectPlay id range used by Phase B's queue-injection spawn flow.

These are all knobs that can change without re-reverse-engineering the engine
(unlike `addresses.py`). Keep raw engine VAs out of this module.
"""

from .build import SectionSpec


# --- new section parameters (.zaxbot) -------------------------------------
NEW_SECTION_NAME   = b'.zaxbot\x00'
NEW_SECTION_VA     = 0x31A000      # RVA; absolute = 0x71A000
NEW_SECTION_SIZE   = 0x5000        # five pages: code + scratch (bumped for movement helpers)
SECTION_CHARACTERS = 0xE0000020    # CODE | EXEC | READ | WRITE
HOOK_ENTRY_OFF     = 0x000
SCRATCH_OFF        = 0x3000        # writable scratch buffer; 12KB code / 8KB scratch

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
# Default 1/60 because the engine ticks at ~60Hz under Wine; if frames slow
# down, the dt term cancels algebraically in
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

# Stuck detection: if (x-last_x)² + (y-last_y)² stays under STUCK_DELTA_SQ for
# STUCK_FRAMES_THRESHOLD consecutive frames, force a retarget. Threshold is
# generous — bots animate idle frames with tiny float jitter even when truly
# stuck against a wall.
STUCK_FRAMES_THRESHOLD = 30
STUCK_DELTA_SQ         = 4.0

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
# 1.0 is a safe-ish starting point — well under typical model max (which
# AGENTS.md notes as 300..4000 px/sec at +0x60 of CModel). Halve/double
# empirically. If the bot looks slow but stationary, raise; if it streaks
# across the map and crashes, lower.
BOT_MOVE_SPEED = 1.0

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
