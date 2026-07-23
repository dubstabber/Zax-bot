"""World-data loaders and periodic scans emitted into ``.zaxbot``.

Package split of the former monolithic ``world_scan.py`` — one module per
world-data domain. ``emit`` order is load-bearing: it fixes the absolute
label positions inside the section, so do not reorder without
re-establishing a byte-identity baseline.

Modules (in emit order):
- ``hazard_pickup`` — ``scan_hazards`` + ``pick_pickup`` entity-array scans.
- ``portals``       — ``load_portals`` + ``bind_portal_nodes``.
- ``flags``         — ``load_flags`` (CTF base anchors).
- ``doors``         — ``load_doors`` / ``door_capture_wedge`` /
                      ``door_refresh_state`` / ``build_edge_doors``.
- ``plasma``        — lava tile-map capture + point/census queries.
- ``switches``      — ``load_switches`` / ``switch_blocked_census`` /
                      ``switch_wander_check``.
- ``sk``            — ``load_sk`` (Salvage King minerals + bins).
- ``items``         — ``load_items`` (filler-item anchors).
- ``goody``         — ``goody_scan_piles`` / ``goody_scan_items``.
- ``mines``         — ``load_mine`` / ``mine_tick`` (proximity-mine ring).
"""

from ...asm import Asm
from ...layout import ScratchLayout
from . import doors, flags, goody, hazard_pickup, items, mines, plasma, portals, sk, switches


def emit(a: Asm, layout: ScratchLayout) -> None:
    hazard_pickup.emit(a, layout)
    portals.emit(a, layout)
    flags.emit(a, layout)
    doors.emit(a, layout)
    plasma.emit(a, layout)
    switches.emit(a, layout)
    sk.emit(a, layout)
    items.emit(a, layout)
    goody.emit(a, layout)
    mines.emit(a, layout)
