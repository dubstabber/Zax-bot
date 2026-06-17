"""Build-time extraction of map teleport trigger centers from Data.dat.

Zax's Data.dat is not a complete ZIP archive, but it stores assets as ZIP
local-file records. The shipped .zax map payloads are uncompressed text. This
module scans those records and finds touching-polygon triggers that perform a
teleport/relocate action with a warp effect. The returned points are source
trigger centers, not the post-teleport destinations, so bots can route into the
actual portal volume.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import struct


LOCAL_FILE_SIG = b'PK\x03\x04'


def _iter_local_files(data: bytes):
    pos = 0
    size = len(data)
    while True:
        off = data.find(LOCAL_FILE_SIG, pos)
        if off < 0 or off + 30 > size:
            return

        try:
            _, _, method, _, _, _, csize, _, name_len, extra_len = struct.unpack_from(
                '<HHHHHIIIHH', data, off + 4
            )
        except struct.error:
            return

        name_start = off + 30
        name_end = name_start + name_len
        data_start = name_end + extra_len
        data_end = data_start + csize

        if (
            0 < name_len < 300
            and name_end <= size
            and data_end <= size
            and csize < 20_000_000
        ):
            name = data[name_start:name_end].decode('latin1', 'replace')
            if method == 0:
                yield name, data[data_start:data_end]
            pos = data_end
        else:
            pos = off + 4


def _find_block(lines: list[str], line_idx: int) -> tuple[int, int]:
    brace_idx = line_idx
    while brace_idx < len(lines) and lines[brace_idx].strip() != '{':
        brace_idx += 1
    if brace_idx >= len(lines):
        return line_idx, min(line_idx + 1, len(lines))

    depth = 0
    for idx in range(brace_idx, len(lines)):
        stripped = lines[idx].strip()
        if stripped == '{':
            depth += 1
        elif stripped == '}':
            depth -= 1
            if depth == 0:
                return line_idx, idx + 1
    return line_idx, len(lines)


def _parse_level_part_destinations(lines: list[str]) -> dict[str, list[tuple[float, float, bool]]]:
    destinations: dict[str, list[tuple[float, float, bool]]] = {}
    idx = 0
    while idx < len(lines):
        if lines[idx].strip() != 'Level Part=CEntityBase':
            idx += 1
            continue

        start, end = _find_block(lines, idx)
        name = None
        x = None
        y = None
        is_spawn_point = False
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
            elif line == 'Activity=CSpawnPointAI':
                is_spawn_point = True

        if name and x is not None and y is not None:
            destinations.setdefault(name, []).append((x, y, is_spawn_point))
        idx = end
    return destinations


_PORTAL_ACTION_LINES = {
    'Action=CRelocateAction',
    'Enter Action=CRelocateAction',
    'Next Action=CRelocateAction',
    'Action=CTeleportAction',
    'Enter Action=CTeleportAction',
    'Next Action=CTeleportAction',
}


def _parse_polygon_center(line: str) -> tuple[float, float] | None:
    if not line.startswith('Polygon='):
        return None

    try:
        values = [float(part.strip()) for part in line.split('=', 1)[1].split(',') if part.strip()]
    except ValueError:
        return None

    if len(values) < 4 or len(values) % 2:
        return None

    points = list(zip(values[0::2], values[1::2]))
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if not points:
        return None

    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _parse_level_part_source_center(
    lines: list[str],
    start: int,
    end: int,
) -> tuple[float, float] | None:
    x = None
    y = None
    for raw in lines[start:end]:
        line = raw.strip()
        polygon_center = _parse_polygon_center(line)
        if polygon_center is not None:
            return polygon_center

        if line.startswith('Position X=') and x is None:
            try:
                x = float(line.split('=', 1)[1])
            except ValueError:
                pass
        elif line.startswith('Position Y=') and y is None:
            try:
                y = float(line.split('=', 1)[1])
            except ValueError:
                pass

    if x is not None and y is not None:
        return x, y
    return None


def _level_part_has_portal_action(lines: list[str], start: int, end: int) -> bool:
    return any(line.strip() in _PORTAL_ACTION_LINES for line in lines[start:end])


def _action_has_warp_effect(
    action_line: str,
    lines: list[str],
    start: int,
    end: int,
    level_part_end: int,
) -> bool:
    if 'CTeleportAction' in action_line:
        return True

    block = '\n'.join(lines[start:end])
    window = '\n'.join(lines[start:min(len(lines), level_part_end, end + 90)])
    return (
        'Warp Behavior=CEntityBehaviorWarpEffect' in block
        or (
            'CAddEffectAction' in window
            and 'Warp Behavior=CEntityBehaviorWarpEffect' in window
        )
    )


def _parse_portals_from_zax(payload: bytes) -> list[tuple[float, float]]:
    text = payload.decode('latin1', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')
    portals: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    idx = 0

    while idx < len(lines):
        if not lines[idx].strip().startswith('Level Part='):
            idx += 1
            continue

        start, end = _find_block(lines, idx)
        if not _level_part_has_portal_action(lines, start, end):
            idx = end
            continue

        center = _parse_level_part_source_center(lines, start, end)
        if center is None:
            idx = end
            continue

        for action_idx in range(start, end):
            action_line = lines[action_idx].strip()
            if action_line not in _PORTAL_ACTION_LINES:
                continue

            action_start, action_end = _find_block(lines, action_idx)
            # A CTeleportAction (or a CRelocateAction carrying a warp effect) IS
            # a portal regardless of whether its destination name resolves to a
            # Level Part in this same file. Earlier code required
            # ``New Location`` to match a parsed destination, which silently
            # dropped every script/event-driven teleporter whose target is a
            # spawn point / named marker / runtime-resolved entity (e.g. the
            # "Upper"/"Lower" pairs on Jungle Ruins) — exactly the conditional
            # portals this table is meant to surface. The warp-effect check
            # (always true for CTeleportAction; explicit for CRelocateAction)
            # is the real "this teleports a player" gate.
            if not _action_has_warp_effect(action_line, lines, action_start, action_end, end):
                continue

            x, y = center
            key = (round(x * 1000), round(y * 1000))
            if key not in seen:
                seen.add(key)
                portals.append((x, y))
            break
        idx = end

    return portals


@lru_cache(maxsize=1)
def resolve_portal_data(data_path: str | None = None) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    """Return ``((map_name, ((x, y), ...)), ...)`` parsed from Data.dat.

    Missing Data.dat is treated as "no static portal data" so unit tests and
    tooling can still build a patched section from just the executable.

    Scoped to MULTIPLAYER maps only. The runtime consumer (load_portals) runs
    from detour_df90, which only fires on the MP per-player spawn path, and the
    overlay that draws portals is itself MP-gated — so a single-player map's
    portals could never load or render. Including them would only bloat the
    fixed scratch table: of the ~54 shipped maps that author warp teleporters,
    only 2 are multiplayer (Hydro Vengence CTF, Jungle Ruins DM), and packing
    all 54 overflows the .zaxbot scratch area. Single-player portal support
    would need its own (non-MP-gated) load path plus a larger section, so it is
    deliberately out of scope here. Drop the filter and grow the section to
    re-enable it.
    """
    path = Path(data_path) if data_path else Path(__file__).resolve().parents[1] / 'Data.dat'
    if not path.exists():
        return ()

    data = path.read_bytes()
    maps: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    for name, payload in _iter_local_files(data):
        if not name.lower().endswith('.zax'):
            continue
        if 'multiplayer' not in name.lower():
            continue
        portals = _parse_portals_from_zax(payload)
        if portals:
            maps.append((name, tuple(portals)))
    return tuple(sorted(maps, key=lambda item: item[0].lower()))
