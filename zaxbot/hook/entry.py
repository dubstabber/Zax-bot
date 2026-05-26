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
from ..layout import build_scratch_layout
from ..static_data import write_static_scratch_data
from . import aim_lead, apply_colors, detect_mode, dispatcher, snapshot, spawn, weapon_speed
from .helpers import emit_logc_body, emit_wbuf_body
from ..detours import (
    bot_fire_aim,
    bot_movement,
    bot_perception,
    char_iter,
    df90_match_change,
    dp_poll,
    name_block,
    spawn_safety,
    walk_controller,
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
    layout = build_scratch_layout(
        scratch_va,
        cfg.NEW_SECTION_SIZE - cfg.SCRATCH_OFF,
        cfg.NUM_BOT_NAMES,
        cfg.NAME_SLOT_SIZE,
        cfg.NAME_SLOT_ASCII,
        cfg.WEAPON_SPEEDS_MAX,
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

    code = a.link()
    assert len(code) <= cfg.SCRATCH_OFF, (
        f'hook code overflows scratch: {len(code):#x}'
    )

    section = bytearray(cfg.NEW_SECTION_SIZE)
    section[cfg.HOOK_ENTRY_OFF:cfg.HOOK_ENTRY_OFF + len(code)] = code
    write_static_scratch_data(
        section,
        cfg.SCRATCH_OFF,
        layout,
        dump_filename=cfg.DUMP_FILENAME,
        dump_msg=cfg.DUMP_MSG,
        step_filename=cfg.STEP_FILENAME,
        full_msg=cfg.FULL_MSG,
        dump_magic=cfg.DUMP_MAGIC,
        dump_tag_len=cfg.DUMP_TAG_LEN,
        bot_names=cfg.BOT_NAMES,
        name_slot_size=cfg.NAME_SLOT_SIZE,
        name_slot_ascii=cfg.NAME_SLOT_ASCII,
        bot_colors=cfg.BOT_COLORS,
        prompt_dm_va=layout.va('prompt_dm'),
        prompt_ctf_va=layout.va('prompt_ctf'),
        prompt_sk_va=layout.va('prompt_sk'),
        fire_range_sq=cfg.FIRE_RANGE_SQ,
        projectile_speed=cfg.PROJECTILE_SPEED,
        weapon_speeds=cfg.WEAPON_SPEEDS,
        force_bot_item_name=cfg.resolve_force_bot_item_name(),
        force_mode=cfg.FORCE_MODE,
    )

    info = {
        'hook_entry_va': section_va_abs + cfg.HOOK_ENTRY_OFF,
        'hook_entry_size': len(code),
        'scratch_va': scratch_va,
        'msg_va': layout.va('msg'),
    }
    for label, key in _DETOUR_LABEL_KEYS.items():
        info[key] = section_va_abs + a.labels[label]
    return bytes(section), info
