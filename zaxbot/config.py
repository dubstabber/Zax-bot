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
SCRATCH_OFF        = 0x1A00        # writable scratch buffer; ~6.5KB code / ~5.5KB scratch

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

# Per-weapon projectile speeds, keyed by the weapon-class vtable VA at
# [weapon + 0x00]. Same vtable for every player holding the same weapon
# class, so this is a stable identifier (the previous +0x20 keying was
# per-instance noise — different value per player). Populate by
# observation: fire each weapon in-game, press R, read `current_proto_va`
# from the weapon_info snapshot chunk (despite the legacy name, it now
# holds the vtable VA), and add a (vtable_va, speed) entry here.
# Unrecognised vtables fall back to PROJECTILE_SPEED.
#
# HITSCAN: use speed = 0.0 as the sentinel — the dispatcher will set
# is_hitscan and skip apply_lead entirely. Known so far (confirmed via
# host/PC2 snapshot diff):
#   0x005EE474 — Rocket Launcher class
WEAPON_SPEEDS = []  # type: list[tuple[int, float]]
# Slots reserved in scratch for the runtime lookup; bumping this only costs
# bytes in .zaxbot scratch, so keep some headroom.
WEAPON_SPEEDS_MAX = 32

# --- Testing knob: force-equip every freshly-spawned bot with this item -----
# Bots default to whatever the engine's "Players Initial Inventory" gives
# them — and since they don't move, they can't pick up replacements. This
# knob is the only way to exercise per-weapon prediction with different
# weapons. Set to an integer item id; sub_425590 will equip the bot with
# that weapon right after spawn (replacing its default Primary). Discover
# usable item ids by iteration: 0, 1, 2, … until the bot fires the weapon
# you want. The bot's R-press `weapon_info` snapshot confirms which
# projectile prototype VA each item id maps to. None disables the override.
FORCE_BOT_ITEM_ID = None  # type: int | None

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
