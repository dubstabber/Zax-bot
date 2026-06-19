"""Build-time extraction of CTF flag home positions from Data.dat.

Mirrors ``portal_data.py``. Each multiplayer ``.zax`` map authors its capture-
the-flag bases as ``Level Part`` markers named ``"Red Flag Spawn"`` /
``"Blue Flag Spawn"`` (``CEntityBase``) carrying a ``Position X`` / ``Position
Y``. Those spawn anchors are the flag HOME positions — the stable points CTF
bots route to (carry the enemy flag back to your home base). The live flag
entity is a ``CEntityAnimated`` tracked by the CTF gametype and is not cleanly
enumerable from the world spatial grid, so the authored anchors are the right
foundation. The returned points are the flag-base centers per map.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .portal_data import _iter_local_files, _find_block


def _parse_flags_from_zax(payload: bytes) -> list[tuple[float, float, int]]:
    """Return ``[(x, y, team), ...]`` for each flag-base anchor in this map.

    ``team`` is 0 for Blue, 1 for Red (the engine's CTF team ids, ``stats+0x14``),
    derived from the anchor name ("Red Flag Spawn" / "Blue Flag Spawn"). The
    runtime maps a bot's own team to its HOME base (``flag_team == bot_team``)
    and the other to the ENEMY base; file order is NOT a reliable Red/Blue
    ordering, so the team must be tagged explicitly here.
    """
    text = payload.decode('latin1', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')
    flags: list[tuple[float, float, int]] = []
    seen: set[tuple[int, int]] = set()
    idx = 0

    while idx < len(lines):
        if not lines[idx].strip().startswith('Level Part='):
            idx += 1
            continue

        start, end = _find_block(lines, idx)
        name = None
        x = None
        y = None
        for raw in lines[start:end]:
            line = raw.strip()
            if line.startswith('Name=') and name is None:
                name = line.split('=', 1)[1]
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

        # A flag-base anchor: name like "Red Flag Spawn" / "Blue Flag Spawn".
        if (
            name
            and 'Flag' in name
            and name.endswith('Spawn')
            and x is not None
            and y is not None
        ):
            key = (round(x * 1000), round(y * 1000))
            if key not in seen:
                seen.add(key)
                team = 1 if name.startswith('Red') else 0  # Red=1, Blue=0
                flags.append((x, y, team))
        idx = end

    return flags


@lru_cache(maxsize=1)
def resolve_flag_data(data_path: str | None = None) -> tuple[tuple[str, tuple[tuple[float, float, int], ...]], ...]:
    """Return ``((map_name, ((x, y, team), ...)), ...)`` parsed from Data.dat.

    Missing Data.dat is treated as "no static flag data" so unit tests and
    tooling can build a patched section from just the executable.

    Scoped to MULTIPLAYER maps only. The engine can run CTF mode on at least one
    map stored under ``Levels/Multiplayer/DeathMatch`` (live-verified:
    Hydroplant Bouncefest), and that map's Red/Blue flag anchors are the points
    the CTF game type uses. Folder name alone is therefore not a valid CTF gate;
    the presence of one Red and one Blue flag anchor is.
    """
    path = Path(data_path) if data_path else Path(__file__).resolve().parents[1] / 'Data.dat'
    if not path.exists():
        return ()

    data = path.read_bytes()
    maps: list[tuple[str, tuple[tuple[float, float, int], ...]]] = []
    for name, payload in _iter_local_files(data):
        if not name.lower().endswith('.zax'):
            continue
        normalized_name = name.replace('\\', '/').lower()
        if '/multiplayer/' not in normalized_name:
            continue
        flags = _parse_flags_from_zax(payload)
        if flags:
            maps.append((name, tuple(flags)))
    return tuple(sorted(maps, key=lambda item: item[0].lower()))
