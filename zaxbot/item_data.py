"""Build-time extraction of pursuable FILLER items (health/energy/shield)
from Data.dat.

Mirrors ``sk_data.py``, but scoped to ALL multiplayer maps and to the three
item categories a bot benefits from walking to (census 2026-07-19, pinned in
tests): the item's identity is baked into its model, and the model PATH
prefix is a clean category discriminator —

* ``Items/Medical/``  -> HEALTH  (fruit / fruit piles, 58 shipped)
* ``Items/Energy/``   -> ENERGY  (battery charges/levels, 128 shipped)
* ``Items/Shields/``  -> SHIELD  (shield charges/belts, 86 shipped)

Weapon/ammo pickups (``Items/Weapons/``) are deliberately excluded — bots
carry a default loadout and collect ammo by walk-over anyway — as are keys
and the SK minerals (``Items/Money/``, owned by the SK layer). Positions are
the authored Level Part ``Position X/Y`` like every other static pipeline.
These anchors feed the per-category multi-source routing fields the
generalized goody-pursuit descends (graph-safe divert instead of the removed
straight-steer pickup divert that ground bots into walls).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .portal_data import _iter_local_files, _find_block


ITEM_CAT_HEALTH = 0
ITEM_CAT_ENERGY = 1
ITEM_CAT_SHIELD = 2
ITEM_CATEGORIES = 3

_CAT_BY_PREFIX = (
    ('items/medical/', ITEM_CAT_HEALTH),
    ('items/energy/',  ITEM_CAT_ENERGY),
    ('items/shields/', ITEM_CAT_SHIELD),
)


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
        for prefix, cat in _CAT_BY_PREFIX:
            if model_l.startswith(prefix):
                key = (round(x * 1000), round(y * 1000))
                if key not in seen:
                    seen.add(key)
                    items.append((x, y, cat))
                break

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
