"""Build-time extraction of pursuable FILLER items (health/energy/shield)
from Data.dat.

Mirrors ``sk_data.py``, but scoped to ALL multiplayer maps and to the three
item categories a bot benefits from walking to (census 2026-07-19, pinned in
tests): the item's identity is baked into its model, and the model PATH
prefix is a clean category discriminator —

* ``Items/Medical/``  -> HEALTH  (fruit / fruit piles, 58 shipped)
* ``Items/Energy/``   -> ENERGY  (battery charges/levels, 128 shipped)
* ``Items/Shields/``  -> SHIELD  (shield charges/belts, 86 shipped)
* WEAPON — gun-granting pickups only (220 shipped), matched by an explicit
  model SET (census 2026-07-23), NOT the ``Items/Weapons/`` prefix: more
  than half the parts under that prefix are AMMO packs (``PU Semi Auto
  Ammo``, ``PU Grenade Canister``, ``PU Missile 5 Pack``, ``PU Proximity
  Mine`` — 255 of 475), which stay walk-over-only. The set INCLUDES
  ``PU Light Pistol`` (70): the actual spawn loadout is the (very weak)
  Modified Laser Welder — user-corrected 2026-07-23 — so even the pistol
  is a genuine upgrade. The weapon category exists so bots PRIORITIZE
  arming up (ranked above the fillers, below the objective pursuits).

Pure ammo pickups are deliberately excluded — bots collect ammo by
walk-over anyway — as are keys and the SK minerals (``Items/Money/``,
owned by the SK layer). Positions are the authored Level Part
``Position X/Y`` like every other static pipeline. These anchors feed the
per-category multi-source routing fields the generalized goody-pursuit
descends (graph-safe divert instead of the removed straight-steer pickup
divert that ground bots into walls).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .portal_data import _iter_local_files, _find_block


ITEM_CAT_HEALTH = 0
ITEM_CAT_ENERGY = 1
ITEM_CAT_SHIELD = 2
ITEM_CAT_WEAPON = 3
ITEM_CATEGORIES = 4

_CAT_BY_PREFIX = (
    ('items/medical/', ITEM_CAT_HEALTH),
    ('items/energy/',  ITEM_CAT_ENERGY),
    ('items/shields/', ITEM_CAT_SHIELD),
)

# Gun-granting pickup models (lowercased). Explicit include SET — the
# Items/Weapons/ prefix also covers the ammo packs, which must NOT be
# pursued (see the module docstring).
_WEAPON_PICKUP_MODELS = frozenset((
    'items/weapons/power ups/pu light pistol',
    'items/weapons/power ups/pu semi auto pistol',
    'items/weapons/power ups/pu full auto pistol',
    'items/weapons/power ups/pu twin disrupter',
    'items/weapons/power ups/pu grenadelauncher',
    'items/weapons/power ups/pu missile launcher',
    'items/weapons/power ups/pu impaction cannon',
    'items/weapons/power ups/pu tri spread gun',
    'items/weapons/power ups/pu alienelecweapcomplete',
    'items/weapons/power ups/pu 02 alienelecweapcomplete',
    'items/weapons/power ups/pu heavybarrellcomplete',
    'items/weapons/power ups/pu megafusioncomplete',
    'items/weapons/power ups/pu nucleardisrcomplete',
    'items/weapons/power ups/pu psyonic wave glove',
))


@dataclass(frozen=True)
class MapItemData:
    map_name: str
    items: tuple[tuple[float, float, int], ...]  # (x, y, category)


def _parse_map_items(map_name: str, payload: bytes) -> MapItemData | None:
    text = payload.decode('latin1', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')

    items: list[tuple[float, float, int]] = []
    seen: set[tuple[int, int]] = set()
    idx = 0
    while idx < len(lines):
        if not lines[idx].strip().startswith('Level Part='):
            idx += 1
            continue
        start, end = _find_block(lines, idx)
        model = None
        x = y = None
        has_pickup = False
        for raw in lines[start:end]:
            line = raw.strip()
            if line.startswith('Model=') and model is None:
                model = line.split('=', 1)[1]
            elif line.startswith('Position X=') and x is None:
                try:
                    x = float(line.split('=', 1)[1])
                except ValueError:
                    pass
            elif line.startswith('Position Y=') and y is None:
                try:
                    y = float(line.split('=', 1)[1])
                except ValueError:
                    pass
            elif line in ('Activity=COverridePickupAI', 'Activity=CPickupAI'):
                has_pickup = True
        idx = end

        if not has_pickup or x is None or y is None:
            continue
        model_l = (model or '').lower()
        cat = None
        for prefix, prefix_cat in _CAT_BY_PREFIX:
            if model_l.startswith(prefix):
                cat = prefix_cat
                break
        if cat is None and model_l in _WEAPON_PICKUP_MODELS:
            cat = ITEM_CAT_WEAPON
        if cat is not None:
            key = (round(x * 1000), round(y * 1000))
            if key not in seen:
                seen.add(key)
                items.append((x, y, cat))

    if not items:
        return None
    return MapItemData(map_name=map_name, items=tuple(items))


@lru_cache(maxsize=1)
def resolve_item_data(data_path: str | None = None) -> tuple[MapItemData, ...]:
    """Per-map filler-item anchors ((x, y, category) each). Missing Data.dat
    is treated as "no static item data"; scoped to MULTIPLAYER maps like
    every other static pipeline. No ``Used In`` filtering: fillers are
    authored for all modes on the shipped maps, and a mode-absent anchor
    would only cost a bot one cooldown-bounded empty visit."""
    path = Path(data_path) if data_path else Path(__file__).resolve().parents[1] / 'Data.dat'
    if not path.exists():
        return ()

    data = path.read_bytes()
    maps: list[MapItemData] = []
    for name, payload in _iter_local_files(data):
        if not name.lower().endswith('.zax'):
            continue
        normalized_name = name.replace('\\', '/').lower()
        if '/multiplayer/' not in normalized_name:
            continue
        parsed = _parse_map_items(name, payload)
        if parsed is not None:
            maps.append(parsed)
    return tuple(sorted(maps, key=lambda item: item.map_name.lower()))
