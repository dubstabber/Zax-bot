"""Build-time extraction of Salvage King (SK / "Greed") data from Data.dat.

Mirrors ``flag_data.py`` / ``door_data.py``. Two SK authoring facts drive the
whole layer (census 2026-07-19, pinned in tests):

* **Minerals** (the ore/crystal pickups SK players collect) are ``Level
  Part=CEntityBase`` blocks whose ``Model=`` is ``Items/Money/Ore deposit N``
  or ``Items/Money/Crystal NN`` and whose ``Used In`` is exactly
  ``MultiPlayer/Salvage King`` — they exist ONLY in SK matches. The item
  identity ("Ore Deposits" / "Crystals") is baked into the model's own
  CPickupAI, not the map text, so the Model prefix IS the discriminator.
  They respawn in place (``COverridePickupAI`` Respawn Delay 10-15 s) and are
  DENSE (100-386 per map), which is why the runtime layer routes to mineral
  *areas* (a per-match multi-source BFS over the bound graph nodes) rather
  than tracking per-pickup presence.
* **Bins** (each player's deposit collector) are ``Level Part=CEntityAnimated``
  blocks named ``Bin NN`` carrying a ``CollideTriggerAI`` whose action runs
  the canned object ``Canned Objects/Drop Ore in Container``. The per-player
  binding is authored explicitly: ``Team Number = NN - 1``, bins are numbered
  contiguously ``Bin 01..Bin <MaxPlayers>``, and the deposit canned gates on
  ``CIsOnSameTeamAction($Instigator, $Trigger)`` — so the bin whose Team
  equals a bot's team id (``stats+0x14``, = botidx in SK) is that bot's one
  and only scoring target, known statically per map.

9 maps author SK data: the 8 under ``Levels/Multiplayer/Greed/`` plus
``Levels/Multiplayer/DeathMatch/Jungle Ruins.zax`` (fully SK-authored, 10
bins + 288 minerals). Census peaks: 386 minerals / 16 bins (The Foundry).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .portal_data import _iter_local_files, _find_block


# Mineral model prefixes (the pile model "Items/Money/Ore_Crystals01" is never
# authored in a map — piles are runtime-spawned by CDropAllOreAndCrystalsAction
# — but exclude it defensively anyway).
_ORE_MODEL_PREFIX     = 'items/money/ore deposit'
_CRYSTAL_MODEL_PREFIX = 'items/money/crystal'

_BIN_NAME_RE = re.compile(r'^bin (\d+)$')
_BIN_CANNED  = 'canned objects/drop ore in container'


@dataclass(frozen=True)
class MapSkData:
    map_name: str
    minerals: tuple[tuple[float, float, int], ...]  # (x, y, kind) kind: 0=ore, 1=crystal
    bins: tuple[tuple[float, float, int], ...]      # (x, y, team) team = authored NN-1


def _parse_map_sk(map_name: str, payload: bytes) -> MapSkData | None:
    text = payload.decode('latin1', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')

    minerals: list[tuple[float, float, int]] = []
    bins: list[tuple[float, float, int]] = []
    seen_minerals: set[tuple[int, int]] = set()
    seen_bins: set[int] = set()
    idx = 0
    while idx < len(lines):
        if not lines[idx].strip().startswith('Level Part='):
            idx += 1
            continue
        start, end = _find_block(lines, idx)
        name = model = None
        x = y = None
        team = None
        has_bin_canned = False
        for raw in lines[start:end]:
            line = raw.strip()
            if line.startswith('Name=') and name is None:
                name = line.split('=', 1)[1]
            elif line.startswith('Model=') and model is None:
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
            elif line.startswith('Team Number=') and team is None:
                team = line.split('=', 1)[1]
            elif line.startswith('Canned Object='):
                if line.split('=', 1)[1].strip().lower() == _BIN_CANNED:
                    has_bin_canned = True
        idx = end

        if x is None or y is None:
            continue
        model_l = (model or '').lower()
        if model_l.startswith(_ORE_MODEL_PREFIX) or model_l.startswith(_CRYSTAL_MODEL_PREFIX):
            key = (round(x * 1000), round(y * 1000))
            if key not in seen_minerals:
                seen_minerals.add(key)
                kind = 0 if model_l.startswith(_ORE_MODEL_PREFIX) else 1
                minerals.append((x, y, kind))
            continue
        m = _BIN_NAME_RE.match((name or '').strip().lower())
        if m and has_bin_canned:
            bin_team = int(m.group(1)) - 1
            if bin_team >= 0 and bin_team not in seen_bins:
                seen_bins.add(bin_team)
                bins.append((x, y, bin_team))

    if not minerals and not bins:
        return None
    # Bins in TEAM order — the runtime table is indexed by team id.
    bins.sort(key=lambda b: b[2])
    return MapSkData(map_name=map_name, minerals=tuple(minerals), bins=tuple(bins))


@lru_cache(maxsize=1)
def resolve_sk_data(data_path: str | None = None) -> tuple[MapSkData, ...]:
    """Per-map SK data (mineral spawn anchors + team-bound bins).

    Missing Data.dat is treated as "no static SK data" so unit tests and
    tooling can build a patched section from just the executable. Scoped to
    MULTIPLAYER maps only, like every other static pipeline (minerals/bins are
    ``Used In=MultiPlayer/Salvage King`` so they only load in SK matches, but
    Jungle Ruins proves the FOLDER is not a valid gate — the authored content
    is).
    """
    path = Path(data_path) if data_path else Path(__file__).resolve().parents[1] / 'Data.dat'
    if not path.exists():
        return ()

    data = path.read_bytes()
    maps: list[MapSkData] = []
    for name, payload in _iter_local_files(data):
        if not name.lower().endswith('.zax'):
            continue
        normalized_name = name.replace('\\', '/').lower()
        if '/multiplayer/' not in normalized_name:
            continue
        parsed = _parse_map_sk(name, payload)
        if parsed is not None:
            maps.append(parsed)
    return tuple(sorted(maps, key=lambda item: item.map_name.lower()))
