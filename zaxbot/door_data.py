"""Build-time extraction of door positions AND opener topology from Data.dat.

Mirrors ``portal_data.py`` / ``flag_data.py``. Each multiplayer ``.zax`` map
authors its doors as ``Level Part=CEntityAnimated`` blocks carrying an
``Activity=CDoorAI`` line plus the entity ``Position X`` / ``Position Y``.
Those authored positions ARE the runtime door entity's raw ``+0x4C/+0x50``
coordinates (same authoring rule the CTF flag anchors follow), so the runtime
can position-match grid entities against this table and read the door's SOLID
flag (``entity+0x1C & 0x40000`` — set while closed, cleared while open) as a
per-door passable/blocked readback.

OPENERS (v2, for door-aware routing). A closed door is traversable for a bot
only from a side it can OPEN it from. The shipped authoring uses:

* SELF-TRIGGER doors: the door part itself carries a touching trigger whose
  Enter action opens ``Door Name=$trigger`` (walk-up doors — Doom ship,
  Battle on the Ice, Foundry; sometimes wrapped in a same-team conditional).
  Opener point = the door itself => openable from BOTH sides (optimistic for
  the team-gated ones; a wrong-team bot just falls back to the wedge
  machinery).
* Standalone WALK-IN triggers (``CTouchingPolygonTriggerAI`` /
  ``CTouchingOvalTriggerAI`` / ``CPassThroughTriggerAI``, authored
  ``Active=1``) whose action tree opens a door by name — the classic
  one-side "walk close and it opens" volumes.
* ARMING triggers: walk-in triggers whose ``CActivateAction`` targets a part
  (the initially-inactive "Dooropening poly" pad) that itself opens a door —
  the Hydroplant one-way pattern. Opener point = the arming trigger.
* NOT bot-usable (excluded): ``CollideTriggerAI`` wall switches (need
  switch-seek behaviour first), ``CSpawnPointAI`` spawn-opened doors,
  ``CRelayAI`` script relays, ``CRepeatTimerTriggerAI`` auto-cyclers.

Doors with NO authored opener of any kind (all 43 on Torture Chamber) open
via the engine's built-in bump path => traversable from both sides. Doors
with only non-bot-usable openers are NOT traversable while closed (spawn
doors, switch-only doors, timer jaws) — live state flips them passable the
moment something opens them.

Door names are matched case-insensitively (map authors mix 'blue door' /
'Blue Door'); a name can match MULTIPLE door instances (multi-panel doors)
and the engine's by-name resolver opens them all, so openers expand to every
matching instance. ``$trigger`` targets resolve to the carrying part itself.

Verified against the IDA-side census (2026-07-16): 10 multiplayer maps author
CDoorAI doors — Battle on the Ice 2, Curse of the Temple 186, Doom ship 29,
Temple Melee 17, Torture Chamber 43, Hydroplant Bouncefest 4, Jungle Ruins 6,
Temple Deathgrip 26, Corridor of Suffering 16, The Foundry 4 (333 total).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .portal_data import _iter_local_files, _find_block


# Trigger activities a bot can fire by walking (openers it can "use").
_WALK_TRIGGER_ACTIVITIES = (
    'Activity=CTouchingPolygonTriggerAI',
    'Activity=CTouchingOvalTriggerAI',
    'Activity=CPassThroughTriggerAI',
)

# Per-door flag bits packed for the runtime (door_static_flags).
DOOR_FLAG_HAS_ANY_OPENER = 0x01   # some part opens/toggles this door by name/self

# Per-switch class bits packed for the runtime (switch_static_flags). A
# "switch" is a Level Part carrying ``Activity=CollideTriggerAI`` — the
# bumpable wall/floor switches. Census over the shipped Data.dat (2026-07-18):
# ALL 116 MP-map collide switches are ``Triggered By Players=1`` /
# ``Projectiles=0`` / ``Trigger Only Once=0`` / authored ``Active=1`` — every
# one is a repeatable walk-into-it switch a bot can fire by bumping it.
SWITCH_FLAG_OPENS_DOORS  = 0x01   # >=1 COpenDoorAction/CToggleDoorAction pair to a door
SWITCH_FLAG_CLOSES_DOORS = 0x02   # CCloseDoorAction present (trap: jaws/spikes/lockouts)
SWITCH_FLAG_TOGGLE       = 0x04   # door pairs use CToggleDoorAction (re-bump RE-CLOSES!)
SWITCH_FLAG_CANNED       = 0x08   # CUseCannedAction (the Greed/SK deposit 'Bin NN')
SWITCH_FLAG_RELAY        = 0x10   # CTriggerRelayAction (script relay)
SWITCH_FLAG_PLAYER_BUMP  = 0x20   # Triggered By Players=1 (bot can fire it by walking)

# Opener team masks (who can use this opener): bit0 = team 0 (Blue),
# bit1 = team 1 (Red). Openers wrapped in a same-team conditional
# (CConditionalAction whose Try is CIsOnSameTeamAction — the walk-up team
# doors) are usable only by the carrying part's Team Number.
OPENER_TEAM_BOTH = 0x3

# "lights #1-13#" template target names expand to 'lights 1'..'lights 13'
# (the prefix is literal, including any trailing space; Temple Deathgrip's
# 'top guard spike#1-6#' has none). Verified against the authored door names
# on Doom ship / Curse of the Temple / Temple Deathgrip.
_TEMPLATE_RE = re.compile(r'^(.*?)#(\d+)-(\d+)#$')


@dataclass(frozen=True)
class MapDoorData:
    map_name: str
    doors: tuple[tuple[float, float], ...]          # (x, y) per door instance
    flags: tuple[int, ...]                          # parallel per-door flag bits
    openers: tuple[tuple[float, float, int, int], ...]  # (x, y, door_index, team_mask)
    switches: tuple[tuple[float, float, int], ...] = ()   # (x, y, class bits) per switch
    switch_pairs: tuple[tuple[int, int], ...] = ()  # (switch_index, door_index) open/toggle


def _block_fields(lines, start, end):
    """First-level Name/Active/Team/Position/Polygon-center of a Level Part."""
    name = active = x = y = team = None
    poly = None
    for raw in lines[start:end]:
        line = raw.strip()
        if line.startswith('Name=') and name is None:
            name = line.split('=', 1)[1]
        elif line.startswith('Active=') and active is None:
            active = line.split('=', 1)[1]
        elif line.startswith('Team Number=') and team is None:
            team = line.split('=', 1)[1]
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
        elif line.startswith('Polygon=') and poly is None:
            try:
                vals = [float(v) for v in line.split('=', 1)[1].split(',') if v.strip()]
                pts = list(zip(vals[0::2], vals[1::2]))
                if len(pts) > 1 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                if pts:
                    poly = (sum(p[0] for p in pts) / len(pts),
                            sum(p[1] for p in pts) / len(pts))
            except ValueError:
                pass
    return name, active, x, y, poly, team


def _team_mask_of(team_field):
    """Opener team mask from a part's ``Team Number`` (0/1 => that team only
    when the opener is same-team-conditional; anything else => both)."""
    try:
        team = int(team_field)
    except (TypeError, ValueError):
        return OPENER_TEAM_BOTH
    if team in (0, 1):
        return 1 << team
    return OPENER_TEAM_BOTH


def _conditional_ranges(lines, start, end):
    """Line ranges of CConditionalAction blocks whose Try action is a
    same-team check — actions inside open only for the part's own team."""
    ranges = []
    for bi in range(start, end):
        if 'CConditionalAction' in lines[bi]:
            cstart, cend = _find_block(lines, bi)
            blob = '\n'.join(lines[cstart:cend])
            if 'CIsOnSameTeamAction' in blob:
                ranges.append((cstart, cend))
    return ranges


def _door_targets(lines, start, end, team_mask_if_conditional):
    """All door-OPENING targets in the block: ``(name_or_$SELF, team_mask)``.

    Counts ``COpenDoorAction`` AND ``CToggleDoorAction`` (the Torture Chamber
    pillar walls and the Doom ship light walls are switch-TOGGLED — an
    open-capable reference either way). ``CCloseDoorAction`` is not an opener.
    ``$trigger`` resolves to the carrying part itself. Targets inside a
    same-team conditional get the restricted team mask.
    """
    cond_ranges = _conditional_ranges(lines, start, end)
    targets = []
    for bi in range(start, end):
        if 'COpenDoorAction' in lines[bi] or 'CToggleDoorAction' in lines[bi]:
            mask = OPENER_TEAM_BOTH
            for (cs, ce) in cond_ranges:
                if cs <= bi < ce:
                    mask = team_mask_if_conditional
                    break
            ostart, oend = _find_block(lines, bi)
            for ol in lines[ostart:oend]:
                ols = ol.strip()
                if ols.startswith('Door Name='):
                    t = ols.split('=', 1)[1].strip()
                    if t.lower() == '$trigger':
                        targets.append(('$SELF', mask))
                    elif t:
                        targets.append((t.lower(), mask))
                    break
    return targets


def _activate_targets(lines, start, end):
    """All CActivateAction 'Target Name' values in the block (lowercased,
    comma-split — the field can list several names)."""
    out = []
    for bi in range(start, end):
        if 'CActivateAction' in lines[bi]:
            ostart, oend = _find_block(lines, bi)
            for ol in lines[ostart:oend]:
                ols = ol.strip()
                if ols.startswith('Target Name='):
                    for t in ols.split('=', 1)[1].split(','):
                        t = t.strip().lower()
                        if t and not t.startswith('$'):
                            out.append(t)
                    break
    return out


def _parse_map_doors(map_name: str, payload: bytes) -> MapDoorData | None:
    text = payload.decode('latin1', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')

    doors = []            # (x, y)
    door_names = []       # lowercased authored name per door instance
    seen = set()
    parts = []            # every parsed part for the opener passes
    idx = 0
    while idx < len(lines):
        if not lines[idx].strip().startswith('Level Part='):
            idx += 1
            continue
        start, end = _find_block(lines, idx)
        blob = lines[start:end]
        blob_has_door_ai = any(l.strip() == 'Activity=CDoorAI' for l in blob)
        name, active, x, y, poly, team = _block_fields(lines, start, end)
        rec = {
            'name': (name or '').lower(),
            'active': active,
            'pos': poly if poly is not None else (x, y),
            'is_door': False,
            'walk_trigger': any(
                l.strip() in _WALK_TRIGGER_ACTIVITIES for l in blob
            ),
            'door_targets': _door_targets(lines, start, end, _team_mask_of(team)),
            'activate_targets': _activate_targets(lines, start, end),
            'is_switch': any('Activity=CollideTriggerAI' in l for l in blob),
            'player_bump': any(l.strip() == 'Triggered By Players=1' for l in blob),
            'has_close_door': any('CCloseDoorAction' in l for l in blob),
            'has_toggle_door': any('CToggleDoorAction' in l for l in blob),
            'has_canned': any('CUseCannedAction' in l for l in blob),
            'has_relay': any('CTriggerRelayAction' in l for l in blob),
        }
        if blob_has_door_ai and x is not None and y is not None:
            key = (round(x * 1000), round(y * 1000))
            if key not in seen:
                seen.add(key)
                rec['is_door'] = True
                rec['door_index'] = len(doors)
                doors.append((x, y))
                door_names.append((name or '').lower())
        parts.append(rec)
        idx = end

    def doors_named(nm):
        """Door instances matching a target name, with '#a-b#' templates
        expanded ('lights #1-13#' => 'lights 1'..'lights 13')."""
        tm = _TEMPLATE_RE.match(nm)
        if tm:
            prefix, lo, hi = tm.group(1), int(tm.group(2)), int(tm.group(3))
            names = {f'{prefix}{k}' for k in range(lo, hi + 1)}
            return [i for i, dn in enumerate(door_names) if dn in names]
        return [i for i, dn in enumerate(door_names) if dn == nm]

    # Switch pass: every CollideTriggerAI part (the bumpable switches).
    # Position + class bits, plus (switch, door) pairs for each door instance
    # its COpenDoorAction/CToggleDoorAction targets resolve to (the routing-
    # relevant binding: which doors this switch can make passable). CClose
    # targets are deliberately NOT paired — closing is a trap, not an opener —
    # but the class bit records it so behavior can avoid/exploit trap switches.
    switches = []
    switch_pairs = []
    switch_seen = set()
    for rec in parts:
        if not rec['is_switch']:
            continue
        pos = rec['pos']
        if pos[0] is None or pos[1] is None:
            continue
        key = (round(float(pos[0]) * 1000), round(float(pos[1]) * 1000))
        if key in switch_seen:
            continue
        switch_seen.add(key)
        sflags = 0
        if rec['player_bump']:
            sflags |= SWITCH_FLAG_PLAYER_BUMP
        if rec['has_close_door']:
            sflags |= SWITCH_FLAG_CLOSES_DOORS
        if rec['has_toggle_door']:
            sflags |= SWITCH_FLAG_TOGGLE
        if rec['has_canned']:
            sflags |= SWITCH_FLAG_CANNED
        if rec['has_relay']:
            sflags |= SWITCH_FLAG_RELAY
        sw_idx = len(switches)
        pair_doors = set()
        for t, _mask in rec['door_targets']:
            if t == '$SELF':
                continue
            for di in doors_named(t):
                pair_doors.add(di)
        if pair_doors:
            sflags |= SWITCH_FLAG_OPENS_DOORS
            for di in sorted(pair_doors):
                switch_pairs.append((sw_idx, di))
        switches.append((float(pos[0]), float(pos[1]), sflags))

    if not doors and not switches:
        return None

    # Pass 1: which doors have ANY opener authored (any part type — collide
    # switches, spawn triggers, relays, timers included). Doors with none are
    # engine bump-open.
    has_any = [False] * len(doors)
    for rec in parts:
        for t, _mask in rec['door_targets']:
            if t == '$SELF':
                if rec['is_door']:
                    has_any[rec['door_index']] = True
                continue
            for di in doors_named(t):
                has_any[di] = True

    # Pass 2: bot-usable opener points (walk-in triggers only).
    openers = []

    def add_opener(pos, door_idx, mask):
        if pos[0] is None or pos[1] is None:
            return
        for k, (ox, oy, odi, omask) in enumerate(openers):
            if odi == door_idx and ox == float(pos[0]) and oy == float(pos[1]):
                openers[k] = (ox, oy, odi, omask | mask)   # merge team masks
                return
        openers.append((float(pos[0]), float(pos[1]), door_idx, mask))

    part_by_name = {}
    for rec in parts:
        part_by_name.setdefault(rec['name'], []).append(rec)

    for rec in parts:
        # Only walk-in triggers the engine fires from a bot's movement, and
        # only when authored active (the Hydroplant pads are Active=0 until
        # armed — the pad itself is NOT an opener; its arming trigger is).
        if not rec['walk_trigger'] or rec['active'] == '0':
            continue
        # Direct open targets.
        for t, mask in rec['door_targets']:
            if t == '$SELF':
                if rec['is_door']:
                    add_opener(rec['pos'], rec['door_index'], mask)
                continue
            for di in doors_named(t):
                add_opener(rec['pos'], di, mask)
        # Arming chain: this trigger activates a part that itself opens doors.
        for t in rec['activate_targets']:
            for target_part in part_by_name.get(t, ()):
                for dt, mask in target_part['door_targets']:
                    if dt == '$SELF':
                        if target_part['is_door']:
                            add_opener(rec['pos'], target_part['door_index'], mask)
                        continue
                    for di in doors_named(dt):
                        add_opener(rec['pos'], di, mask)

    flags = tuple(DOOR_FLAG_HAS_ANY_OPENER if h else 0 for h in has_any)
    return MapDoorData(
        map_name=map_name,
        doors=tuple(doors),
        flags=flags,
        openers=tuple(openers),
        switches=tuple(switches),
        switch_pairs=tuple(switch_pairs),
    )


@lru_cache(maxsize=1)
def resolve_door_topology(data_path: str | None = None) -> tuple[MapDoorData, ...]:
    """Full per-map door data (positions + flags + bot-usable openers).

    Missing Data.dat is treated as "no static door data" so unit tests and
    tooling can build a patched section from just the executable. Scoped to
    MULTIPLAYER maps only, like the portal/flag pipelines.
    """
    path = Path(data_path) if data_path else Path(__file__).resolve().parents[1] / 'Data.dat'
    if not path.exists():
        return ()

    data = path.read_bytes()
    maps: list[MapDoorData] = []
    for name, payload in _iter_local_files(data):
        if not name.lower().endswith('.zax'):
            continue
        normalized_name = name.replace('\\', '/').lower()
        if '/multiplayer/' not in normalized_name:
            continue
        parsed = _parse_map_doors(name, payload)
        if parsed is not None:
            maps.append(parsed)
    return tuple(sorted(maps, key=lambda item: item.map_name.lower()))


def resolve_door_data(data_path: str | None = None) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    """Back-compat view: ``((map_name, ((x, y), ...)), ...)`` — door maps only
    (the topology also keeps switch-only maps for the switch tables)."""
    return tuple((m.map_name, m.doors) for m in resolve_door_topology(data_path) if m.doors)
