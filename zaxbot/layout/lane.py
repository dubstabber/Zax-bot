"""Attacker route-lane split — ctf_next_hop per-call temps.

``cnh_curd`` holds the current node's field distance as a FIXED descent
threshold (the running best in ECX becomes a MAX under lane 1, so it can
no longer double as the strict-descent gate); ``cnh_lane`` is the
per-call lane mode (0 = min descent, 1 = max descent). The lane BIT
itself lives in ``bot_role`` bit1 (layout/role.py block)."""

from .model import ScratchField


def extend_lane(c):
    fields = c.fields
    overlay_fields = c.overlay_fields
    flag_route_max_capped = c.flag_route_max_capped

    if not flag_route_max_capped:
        return

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0x7) & ~0x7

    overlay_fields.extend([
        ScratchField('cnh_curd', base, 0x04,
                     'lane: ctf_next_hop per-call current-node distance (strict-descent gate)'),
        ScratchField('cnh_lane', base + 0x04, 0x04,
                     'lane: ctf_next_hop per-call descent mode (0 = min/shortest, 1 = max/alternate)'),
    ])
