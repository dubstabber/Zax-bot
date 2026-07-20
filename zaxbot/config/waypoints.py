"""Authored overlay waypoint-graph resolution/validation."""

from .overlay import OVERLAY_EDGES, OVERLAY_EDGE_MAX, OVERLAY_VERTEX_MAX, OVERLAY_WAYPOINTS


def resolve_overlay_data():
    """Validate cfg.OVERLAY_WAYPOINTS and OVERLAY_EDGES; return packed lists.

    Raises ValueError on out-of-range edge indices or capacity overflow.
    Empty lists are valid — the runtime renders nothing in that case.
    """
    waypoints = list(OVERLAY_WAYPOINTS)
    if len(waypoints) > OVERLAY_VERTEX_MAX:
        raise ValueError(
            f'OVERLAY_WAYPOINTS has {len(waypoints)} entries; max '
            f'{OVERLAY_VERTEX_MAX} (raise OVERLAY_VERTEX_MAX)'
        )
    for i, p in enumerate(waypoints):
        if type(p) is not tuple or len(p) != 2:
            raise ValueError(f'OVERLAY_WAYPOINTS[{i}] must be (x, y); got {p!r}')

    edges = list(OVERLAY_EDGES)
    if len(edges) > OVERLAY_EDGE_MAX:
        raise ValueError(
            f'OVERLAY_EDGES has {len(edges)} entries; max '
            f'{OVERLAY_EDGE_MAX} (raise OVERLAY_EDGE_MAX)'
        )
    for k, e in enumerate(edges):
        if type(e) is not tuple or len(e) != 2:
            raise ValueError(f'OVERLAY_EDGES[{k}] must be (i, j); got {e!r}')
        i, j = e
        if not (0 <= i < len(waypoints)) or not (0 <= j < len(waypoints)):
            raise ValueError(
                f'OVERLAY_EDGES[{k}]=({i},{j}) out of range for '
                f'{len(waypoints)} vertices'
            )
        if i > 0xFFFF or j > 0xFFFF:
            raise ValueError(f'OVERLAY_EDGES[{k}] index >0xFFFF not packable as u16')
    return waypoints, edges
