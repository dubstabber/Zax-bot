"""Shared ASM helpers for identifying bot participants at runtime.

Three pieces of bot state are scanned the same way across multiple detours:

  - ``detour_542360`` and ``detour_5436F0`` need to ask "is the controller in
    ECX a bot, and if so which slot?" so they can short-circuit the engine's
    normal input pipeline.
  - ``detour_5436F0`` also needs to ask "given a candidate char's participant
    index, what team is on it?" to apply the CTF same-team filter.

Both questions reduce to a linear scan of ``bot_indices[]`` for a player_num
value. Keeping the scan in one place removes the three near-identical
hand-encoded loops that used to live in the detour modules and makes adding
new bot-state lookups (e.g. AI target cache, path-finding nodes) cheap.

All helpers are pure emitters — they append bytes to the live ``Asm`` cursor
and rely on the caller to provide unique label prefixes so per-call labels do
not collide across detours."""

from .. import config as cfg
from ..asm import Asm, le32


def emit_scan_bot_indices(a: Asm, layout, *, on_no_match: str, label_prefix: str) -> None:
    """Scan ``bot_indices[]`` for the player-num in EDX.

    On match falls through with ``EAX = &bot_indices[slot]``; on no match
    jumps to ``on_no_match``. EDX is preserved. Caller chooses what to do
    with the matched address — convert to slot index, deref a parallel array
    (e.g. ``bot_team``), etc.
    """
    bot_indices_va = layout.va('bot_indices')
    end_va = bot_indices_va + 4 * cfg.MAX_BOT_SLOTS
    a.raw(b'\xB8' + le32(bot_indices_va))            # mov eax, bot_indices
    a.label(f'{label_prefix}_scan')
    a.raw(b'\x3B\x10')                                # cmp edx, [eax]
    a.jz(f'{label_prefix}_hit')
    a.raw(b'\x83\xC0\x04')                            # add eax, 4
    a.raw(b'\x3D' + le32(end_va))                     # cmp eax, bot_indices_end
    a.jb(f'{label_prefix}_scan')
    a.jmp(on_no_match)
    a.label(f'{label_prefix}_hit')


def emit_is_bot_controller(a: Asm, layout, *, on_not_bot: str, label_prefix: str) -> None:
    """ECX = walking controller. Falls through iff this controller belongs to
    one of our bots; otherwise jumps to ``on_not_bot``.

    On success leaves ``EAX = &bot_indices[slot]`` (the same convention as
    ``emit_scan_bot_indices``). EDX is clobbered with the controller's
    player_num. Skips host immediately when player_num == 0.
    """
    a.raw(b'\x8B\x51\x1C')                            # mov edx, [ecx+0x1C] (player_num)
    a.raw(b'\x85\xD2')                                # test edx, edx
    a.jz(on_not_bot)                                  # player_num 0 = host
    emit_scan_bot_indices(a, layout,
                          on_no_match=on_not_bot,
                          label_prefix=label_prefix)


def emit_addr_to_slot(a: Asm, layout) -> None:
    """Convert ``EAX = &bot_indices[slot]`` -> ``EAX = slot`` in-place.

    Use right after ``emit_is_bot_controller`` / ``emit_scan_bot_indices``
    when the caller wants the slot index (not the address) — e.g. to index
    a parallel array with ``[arr + eax*4]``.
    """
    a.raw(b'\x2D' + le32(layout.va('bot_indices')))   # sub eax, bot_indices
    a.raw(b'\xC1\xE8\x02')                            # shr eax, 2
