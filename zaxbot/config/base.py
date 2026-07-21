""".zaxbot section parameters and the zax_dump.bin tagged-chunk format."""

from ..build import SectionSpec


# --- new section parameters (.zaxbot) -------------------------------------
NEW_SECTION_NAME   = b'.zaxbot\x00'
NEW_SECTION_VA     = 0x31A000      # RVA; absolute = 0x71A000
NEW_SECTION_SIZE   = 0x27000       # 44KB code + 112KB scratch (grown for the door detection
                                   # tables, the door-aware routing field, its per-team
                                   # split, the switch detection tables, the portal
                                   # routing layer — dest tables + node bindings — the
                                   # dropped-flag pursuit layer: drop_dist BFS rows —
                                   # then the SK layer: 1856 static mineral anchors,
                                   # per-team bin tables, the mineral field + 16 bin
                                   # BFS rows, and the 512-slot pickup table — then
                                   # +0x1000 code room at the bot-menu GUI polish)
SECTION_CHARACTERS = 0xE0000020    # CODE | EXEC | READ | WRITE
HOOK_ENTRY_OFF     = 0x000
SCRATCH_OFF        = 0xB000        # writable scratch buffer; 44KB code / 112KB scratch
                                   # (boundary moved from 0x5A00 at the door layer, from
                                   # 0x6800 at the switch layer, from 0x7000 at the
                                   # portal-routing layer, from 0x8000 when the
                                   # dropped-flag ROUTED pursuit landed with ~456 code
                                   # bytes left, from 0x9000 at the SK layer with
                                   # ~3.0KB code left, then from 0xA000 at the bot-menu
                                   # GUI polish with 15 code bytes left)

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

