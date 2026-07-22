"""CTF enemy-carrier chase state.

Appended at the very tail so no existing offset shifts. The block from
``chase_pos`` through ``chase_dsq_tmp`` is kept CONTIGUOUS: the df90
match-change clear zeroes it with one rep-stosd and the R-snapshot
``chase`` chunk dumps it whole (tests pin the invariant). The per-flag
BFS rows (``chase_dist``) sit after the tag, excluded from the dump
like ``drop_dist``."""

from .model import ScratchField


def extend_chase(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    flag_route_max_capped = c.flag_route_max_capped
    overlay_vertex_max_capped = c.overlay_vertex_max_capped

    # The chase rides the CTF routing machinery (carrier identity comes from
    # the flag/team tables, the routed phase from bfs_run); without the
    # routing block the fields are simply absent (every consumer gates on
    # layout.has_field).
    if not flag_route_max_capped or not overlay_vertex_max_capped:
        return

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0xF) & ~0xF
    R = flag_route_max_capped

    off = base
    def field(name, size, desc):
        nonlocal off
        f = ScratchField(name, off, size, desc)
        off += size
        return f

    overlay_fields.extend([
        field('chase_pos', R * 8,
              'chase: float[2] last-seen enemy-carrier position per flag idx'),
        field('chase_node', R * 4,
              'chase: nearest graph node to the carrier (-1 = unbound; page-flip bound)'),
        field('chase_ttl', R * 4,
              'chase: sighting memory frames left (page-flip ticked; 0 = no live intel)'),
        field('chase_root', R * 4,
              'chase: node each chase_dist row is currently built from (-1 = row invalid)'),
        field('bot_chase_flag', MAX_BOT_SLOTS * 4,
              'chase: per-bot latched pursuit (flag idx+1, 0 = none)'),
        field('bot_chase_cd', MAX_BOT_SLOTS * 4,
              'chase: per-bot re-latch cooldown (thinks; after a pinned timeout)'),
        field('chase_scan_tmp', 0x04,
              'chase: pick_target per-call home flag idx (-1 = chase scan off this call)'),
        field('chase_dsq_tmp', 0x04,
              'chase: follower per-think dsq(bot, carrier) spill (float bits)'),
        field('tag_chase', 0x10,
              'diag: enemy-carrier chase dump chunk tag'),
        field('chase_dist', R * overlay_vertex_max_capped * 4,
              'chase: per-flag BFS distance rows rooted at the carrier node (excluded from dump)'),
    ])
