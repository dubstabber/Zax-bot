"""CTF attacker/defender role state.

Appended at the very tail so no existing offset shifts. ``bot_role`` /
``role_spawn_count`` / ``defend_radius`` are kept CONTIGUOUS in that
order: the df90 match-change clear zeroes them with one rep-stosd and
the R-snapshot ``role`` chunk dumps them whole (tests pin the
invariant)."""

from .model import ScratchField


def extend_role(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    flag_route_max_capped = c.flag_route_max_capped

    # Roles only gate CTF goal selection; without the routing block there is
    # nothing for a defender to do, so the fields are simply absent (every
    # consumer gates on layout.has_field).
    if not flag_route_max_capped:
        return

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0xF) & ~0xF

    overlay_fields.extend([
        ScratchField('bot_role', base, MAX_BOT_SLOTS * 4,
                     'role: per-bot CTF role (0 = attacker, 1 = defender)'),
        ScratchField('role_spawn_count', base + MAX_BOT_SLOTS * 4, 0x08,
                     'role: per-team CTF spawn counter (alternates roles; reset per match)'),
        ScratchField('defend_radius', base + MAX_BOT_SLOTS * 4 + 0x08,
                     flag_route_max_capped * 4,
                     'role: per-base defender patrol radius in BFS quanta (per match, from map span)'),
        ScratchField('tag_role', base + MAX_BOT_SLOTS * 4 + 0x08
                     + flag_route_max_capped * 4, 0x10,
                     'diag: role/defender state dump chunk tag'),
    ])
