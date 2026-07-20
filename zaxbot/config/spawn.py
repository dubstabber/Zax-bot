"""Spawn-time knobs: force-equip testing item/ammo, FORCE_MODE, the
synthetic DirectPlay id range, bot display names and per-name colors."""

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


