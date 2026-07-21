"""``build_hook`` orchestrator for the .zaxbot section.

Builds the scratch layout, allocates a single ``Asm`` instance, then calls
each emitter in the order their labels appear in the final section. Returns
``(section_bytes, info)`` where ``info`` is the label-VA dict consumed by
``zaxbot.patch_manifest``.

Emit order matters: it determines absolute label positions and thus the
detour-target VAs that are patched back into ``Zax.exe``. Do not reorder
without re-establishing a new byte-identity baseline."""

from .. import config as cfg
from ..asm import Asm
from ..layout import build_layout_from_config
from ..static_data import write_static_from_config
from . import aim_lead, apply_colors, detect_mode, dispatcher, snapshot, spawn, waypoint_diag, waypoint_edit, weapon_speed
from .helpers import emit_logc_body, emit_wbuf_body
from ..detours import (
    bot_fire_aim,
    bot_movement,
    bot_perception,
    char_iter,
    ctf_score_guard,
    df90_match_change,
    dp_poll,
    entity_scan,
    flag_events,
    flag_give_guard,
    flag_route,
    name_block,
    overlay,
    pickup_register,
    portal_register,
    sk_pile_register,
    spawn_safety,
    walk_controller,
    world_scan,
)


# Every label name that ``patch_manifest`` will look up by ``<label>_va`` key.
# Maps the label inside .zaxbot to its info-dict key.
_DETOUR_LABEL_KEYS = {
    'detour_dp':               'detour_dp_va',
    'detour_df90':             'detour_df90_va',
    'detour_5AA4E0':           'detour_5AA4E0_va',
    'detour_4FBC50':           'detour_4FBC50_va',
    'detour_542550':           'detour_542550_va',
    'detour_542360':           'detour_542360_va',
    'detour_5436F0':           'detour_5436F0_va',
    'detour_4EF900_test':      'detour_4EF900_test_va',
    'detour_4FC7C0':           'detour_4FC7C0_va',
    'detour_417390':           'detour_417390_va',
    'detour_5AC299':           'detour_5AC299_va',
    'detour_name_query1':      'detour_name_query1_va',
    'detour_name_query2':      'detour_name_query2_va',
    'detour_name_block_skip':  'detour_name_block_skip_va',
    'detour_4F5204':           'detour_4F5204_va',
    'detour_5693A0':           'detour_5693A0_va',
    'detour_53DA40':           'detour_53DA40_va',
    'detour_4C11A0':           'detour_4C11A0_va',
    'detour_5A9960':           'detour_5A9960_va',
    'detour_4C29F0':           'detour_4C29F0_va',
    'detour_4C2D60':           'detour_4C2D60_va',
    'detour_5A6E60':           'detour_5A6E60_va',
    'detour_5B4DA0':           'detour_5B4DA0_va',
}


def build_hook(section_va_abs):
    """Assemble the .zaxbot section. Returns ``(section_bytes, info)``.

    On B: open a mode-aware text-prompt menu via ``sub_59B260``. On the next
    digit key, spawn a bot bound to the chosen team. ``detect_mode`` calls
    the engine's ``sub_59FF90(ecx=mgr)`` getter for the active game-type
    instance and matches ``[result+0]`` against the three known vtables to
    return 0 (DM), 1 (CTF), or 2 (SK); unknown vtables drop a one-shot
    0x200-byte dump and fall back to DM. ``zaxbot/config.py``'s
    ``FORCE_MODE`` knob short-circuits detection for offline testing.
    """
    scratch_va = section_va_abs + cfg.SCRATCH_OFF
    layout = build_layout_from_config(
        scratch_va,
        cfg.NEW_SECTION_SIZE - cfg.SCRATCH_OFF,
    )

    a = Asm(section_va_abs + cfg.HOOK_ENTRY_OFF)

    # --- Hook payload bodies (order is load-bearing) -----------------------
    dispatcher.emit(a, layout)
    detect_mode.emit(a, layout)
    spawn.emit(a, layout)
    # apply_bot_colors is a callable subroutine used from spawn (post-spawn
    # color application). Emit it immediately after spawn so the call_lbl
    # forward-reference resolves to a nearby site.
    apply_colors.emit(a, layout)
    emit_wbuf_body(a, dummy_va=layout.va('dummy'))
    snapshot.emit(a, layout)
    waypoint_diag.emit(a, layout)
    waypoint_edit.emit(a, layout)
    emit_logc_body(
        a,
        stepfn_va=layout.va('stepfn'),
        dummy_va=layout.va('dummy'),
        logbyte_va=layout.va('logbyte'),
    )

    # --- Detours (emit order = section layout order) -----------------------
    dp_poll.emit(a, layout)
    df90_match_change.emit(a, layout)
    walk_controller.emit(a, layout)
    # world_scan must precede bot_movement so the `call_lbl 'pick_pickup'`
    # forward reference is at a sane distance (matches the `pick_target`
    # precedent — the linker is two-pass so source order isn't required, but
    # adjacency keeps the section grep-friendly).
    world_scan.emit(a, layout)
    bot_movement.emit(a, layout)
    # pick_target must be emitted before bot_fire_aim's detour body so the
    # call_lbl inside detour_5436F0 resolves to a forward-defined label
    # (works either way since Asm.link() is a two-pass linker, but emit
    # order also fixes the absolute VAs, so we keep perception adjacent).
    bot_perception.emit(a, layout)
    aim_lead.emit(a, layout)
    weapon_speed.emit(a, layout)
    bot_fire_aim.emit(a, layout)
    spawn_safety.emit(a, layout)
    name_block.emit(a, layout)
    char_iter.emit(a, layout)
    overlay.emit(a, layout)
    pickup_register.emit(a, layout)
    portal_register.emit(a, layout)
    sk_pile_register.emit(a, layout)
    entity_scan.emit(a, layout)
    flag_events.emit(a, layout)
    flag_route.emit(a, layout)
    ctf_score_guard.emit(a, layout)
    flag_give_guard.emit(a, layout)

    code = a.link()
    assert len(code) <= cfg.SCRATCH_OFF, (
        f'hook code overflows scratch: {len(code):#x}'
    )

    section = bytearray(cfg.NEW_SECTION_SIZE)
    section[cfg.HOOK_ENTRY_OFF:cfg.HOOK_ENTRY_OFF + len(code)] = code
    write_static_from_config(section, cfg.SCRATCH_OFF, layout)

    info = {
        'hook_entry_va': section_va_abs + cfg.HOOK_ENTRY_OFF,
        'hook_entry_size': len(code),
        'scratch_va': scratch_va,
        'msg_va': layout.va('msg'),
    }
    for label, key in _DETOUR_LABEL_KEYS.items():
        info[key] = section_va_abs + a.labels[label]
    return bytes(section), info
