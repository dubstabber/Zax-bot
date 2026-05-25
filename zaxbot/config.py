"""Bot-policy configuration: section layout, scratch sizes, bot names, and the
synthetic DirectPlay id range used by Phase B's queue-injection spawn flow.

These are all knobs that can change without re-reverse-engineering the engine
(unlike `addresses.py`). Keep raw engine VAs out of this module.
"""

from .build import SectionSpec


# --- new section parameters (.zaxbot) -------------------------------------
NEW_SECTION_NAME   = b'.zaxbot\x00'
NEW_SECTION_VA     = 0x31A000      # RVA; absolute = 0x71A000
NEW_SECTION_SIZE   = 0x2000        # two pages: code + scratch (room for ring buffer)
SECTION_CHARACTERS = 0xE0000020    # CODE | EXEC | READ | WRITE
HOOK_ENTRY_OFF     = 0x000
SCRATCH_OFF        = 0x1200        # writable scratch buffer; 4.5KB code / 3.5KB scratch

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
