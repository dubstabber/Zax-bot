"""Build-time extraction of door positions from Data.dat.

Mirrors ``portal_data.py`` / ``flag_data.py``. Each multiplayer ``.zax`` map
authors its doors as ``Level Part=CEntityAnimated`` blocks carrying an
``Activity=CDoorAI`` line plus the entity ``Position X`` / ``Position Y``.
Those authored positions ARE the runtime door entity's raw ``+0x4C/+0x50``
coordinates (same authoring rule the CTF flag anchors follow), so the runtime
can position-match grid entities against this table and read the door's SOLID
flag (``entity+0x1C & 0x40000`` — set while closed, cleared while open) as a
per-door passable/blocked readback.

Only door POSITIONS are extracted for the detection layer. The arming
topology (one-side proximity triggers, wall switches, "Dooropening poly"
pads — see the door-runtime-model notes) is deliberately NOT parsed yet;
it becomes relevant for the routing/switch-seeking stage, not detection.

Verified against the IDA-side census (2026-07-16): 10 multiplayer maps author
CDoorAI doors — Battle on the Ice 2, Curse of the Temple 186, Doom ship 29,
Temple Melee 17, Torture Chamber 43, Hydroplant Bouncefest 4, Jungle Ruins 6,
Temple Deathgrip 26, Corridor of Suffering 16, The Foundry 4 (333 total).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .portal_data import _iter_local_files, _find_block


def _parse_doors_from_zax(payload: bytes) -> list[tuple[float, float]]:
    """Return ``[(x, y), ...]`` for each CDoorAI-carrying Level Part."""
    text = payload.decode('latin1', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')
    doors: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    idx = 0

    while idx < len(lines):
        if not lines[idx].strip().startswith('Level Part='):
            idx += 1
            continue

        start, end = _find_block(lines, idx)
        has_door_ai = False
        x = None
        y = None
        for raw in lines[start:end]:
            line = raw.strip()
            if line == 'Activity=CDoorAI':
                has_door_ai = True
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

        if has_door_ai and x is not None and y is not None:
            key = (round(x * 1000), round(y * 1000))
            if key not in seen:
                seen.add(key)
                doors.append((x, y))
        idx = end

    return doors


@lru_cache(maxsize=1)
def resolve_door_data(data_path: str | None = None) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    """Return ``((map_name, ((x, y), ...)), ...)`` parsed from Data.dat.

    Missing Data.dat is treated as "no static door data" so unit tests and
    tooling can build a patched section from just the executable.

    Scoped to MULTIPLAYER maps only for the same reason the portal/flag
    pipelines are: the runtime consumer (load_doors) only fires on the MP
    per-player spawn path, and packing single-player maps would overflow the
    fixed scratch tables for no runtime benefit.
    """
    path = Path(data_path) if data_path else Path(__file__).resolve().parents[1] / 'Data.dat'
    if not path.exists():
        return ()

    data = path.read_bytes()
    maps: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    for name, payload in _iter_local_files(data):
        if not name.lower().endswith('.zax'):
            continue
        normalized_name = name.replace('\\', '/').lower()
        if '/multiplayer/' not in normalized_name:
            continue
        doors = _parse_doors_from_zax(payload)
        if doors:
            maps.append((name, tuple(doors)))
    return tuple(sorted(maps, key=lambda item: item[0].lower()))
