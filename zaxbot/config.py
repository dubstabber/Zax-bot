"""Bot-policy configuration: section layout, scratch sizes, bot names, and the
synthetic DirectPlay id range used by Phase B's queue-injection spawn flow.

These are all knobs that can change without re-reverse-engineering the engine
(unlike `addresses.py`). Keep raw engine VAs out of this module.
"""

from .build import SectionSpec


# --- new section parameters (.zaxbot) -------------------------------------
NEW_SECTION_NAME   = b'.zaxbot\x00'
NEW_SECTION_VA     = 0x31A000      # RVA; absolute = 0x71A000
NEW_SECTION_SIZE   = 0x4000        # four pages: code + scratch (bumped for diagnostic blocks)
SECTION_CHARACTERS = 0xE0000020    # CODE | EXEC | READ | WRITE
HOOK_ENTRY_OFF     = 0x000
SCRATCH_OFF        = 0x2000        # writable scratch buffer; 8KB code / 8KB scratch

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

# Projectile speed used for shot leading. Units are engine-world units
# per pick_target call (≈ per frame), matching the per-frame delta the
# perception loop uses to estimate target velocity. This is the FALLBACK
# used when the bot's current weapon prototype is not listed in
# WEAPON_SPEEDS below. Hitscan weapons (no projectile) bypass apply_lead
# entirely and ignore this value.
PROJECTILE_SPEED = 10.0

# Per-weapon projectile speeds, keyed by the weapon's inventory-item
# definition pointer returned by sub_4DD480(current_weapon_obj). The generic
# inventory-item vtable at [weapon + 0x00] is shared across different weapons,
# so it is not a usable key. Populate by observation: equip/fire a weapon,
# press R, read the second dword of the `weapon_info`, `host_weapon`, or
# `pc2_weapon` snapshot chunk, and add a (definition_va, speed) entry here.
# Unrecognised definitions fall back to PROJECTILE_SPEED.
#
# HITSCAN: use speed = 0.0 as the sentinel — the dispatcher will set
# is_hitscan and skip apply_lead entirely.
WEAPON_SPEEDS = []  # type: list[tuple[int, float]]
# Slots reserved in scratch for the runtime lookup; bumping this only costs
# bytes in .zaxbot scratch, so keep some headroom.
WEAPON_SPEEDS_MAX = 32

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
FORCE_BOT_ITEM_NAME = 'Nuclear Disruptor'  # type: str | None


def _validate_bot_item_name(value, label):
    if type(value) is not str:
        raise ValueError(f'{label} must be a string item name, got {value!r}')
    data = value.encode('ascii')
    if not data:
        raise ValueError(f'{label} must not be empty')
    if b'\x00' in data:
        raise ValueError(f'{label} must not contain NUL bytes')
    return data + b'\x00'


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
