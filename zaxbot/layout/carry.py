"""Per-bot carry mirror — the carrier ESCAPE-priority gate.

``bot_carry[slot]`` mirrors ``ctf_pick_goal``'s live inventory carry test
per think (the ``route_carry`` GLOBAL is only fresh for whichever bot ran
cpg last, so per-think consumers that run BEFORE this bot's cpg — the
strafe weave, the goody entry — need the per-bot copy; one-think
staleness is fine for behaviour gating). Cleared on respawn (setup.py)
and match change (df90); stays 0 outside CTF."""

from .model import ScratchField


def extend_carry(c):
    MAX_BOT_SLOTS = c.MAX_BOT_SLOTS
    fields = c.fields
    overlay_fields = c.overlay_fields
    flag_route_max_capped = c.flag_route_max_capped

    if not flag_route_max_capped:
        return

    base = max(f.end for f in fields + overlay_fields)
    base = (base + 0x3) & ~0x3

    overlay_fields.append(ScratchField(
        'bot_carry', base, MAX_BOT_SLOTS * 4,
        'carry: per-bot CTF flag-carry mirror (1 = carrying; cpg-written per think)'))
