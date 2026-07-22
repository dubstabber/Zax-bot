"""Need-gated goody pursuit — the per-think pickup-need bitmask.

Refreshed by ``goody_update_need`` (world_scan/goody.py) from the bot's
live state before every goody item scan; bit0 = health, bit1 = energy,
bit2 = shield. Stale values are never consulted (recomputed each think
before use), so no match-change clear is needed."""

from .model import ScratchField


def extend_need(c):
    fields = c.fields
    overlay_fields = c.overlay_fields

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0x3) & ~0x3

    overlay_fields.append(ScratchField(
        'goody_need_mask', base, 0x04,
        'need: per-think filler-need bitmask (bit0 health / bit1 energy / bit2 shield)'))
