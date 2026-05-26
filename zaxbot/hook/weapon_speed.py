"""``compute_proj_speed`` — per-fire-frame weapon dispatch for shot leading.

Replicates the engine's own weapon-lookup chain from ``sub_543830``:

    inv  = sub_4267E0(this = bot_char)                ; inventory
    hash = sub_523DF0(this = SLOT_NAME_REGISTRY,
                      "Primary", -1)                  ; slot hash
    item = sub_425290(this = inv, hash)               ; item id
    wpn  = inv.vtable[+0x68](this = inv, item)        ; weapon object

…then reads ``*(wpn + 0x20)`` (the registered "Projectiles/Projectile"
prototype). A NULL prototype means the weapon is hitscan — the discharge
hits instantly and no lead should be applied. Otherwise the prototype VA
identifies the weapon class; a linear scan over ``weapon_table`` (built
from ``cfg.WEAPON_SPEEDS`` at patch time) selects the right projectile
speed. Unknown prototypes fall back to ``cfg.PROJECTILE_SPEED`` which
remains in ``proj_speed`` from static init.

The ``primary_hash`` value is process-stable, so it's cached on first use
and reused thereafter — only one call per process pays for the string
lookup. Every other field touched here (``inv_tmp``, ``current_weapon_obj``,
``current_proto_va``, ``is_hitscan``, ``proj_speed``) is per-call scratch.

Diagnostic stashes (``current_weapon_obj``, ``current_proto_va``) feed the
``weapon_info`` snapshot chunk so the user can identify each weapon's
prototype VA by switching weapons in-game and pressing R.

Inputs (scratch):
  ``bot_char_tmp`` — bot's char ptr (set by detour_5436F0).
  ``weapon_table`` — packed (proto_va, speed) entries + 0-VA sentinel.

Outputs (scratch):
  ``is_hitscan``        — 1 if weapon has no projectile (skip apply_lead).
  ``proj_speed``        — projectile speed for apply_lead this frame.
  ``current_weapon_obj``— weapon obj ptr (diagnostic).
  ``current_proto_va``  — projectile prototype VA (0 == hitscan, diagnostic).
  ``primary_hash``      — cached "Primary" slot hash (lazy init).
  ``inv_tmp``           — inventory ptr held across sub_523DF0 call.

Side effects: 4 engine calls per fire frame per bot (sub_4267E0, possibly
sub_523DF0 on first call only, sub_425290, inv.vtable[+0x68]). Same chain
sub_543830 itself runs, so the net engine overhead roughly doubles for
those four targets — acceptable.

Clobbers: EAX, ECX, EDX, EBX, ESI. (EBX/ESI saved-restored around engine
calls per Microsoft __thiscall convention — sub_4267E0/sub_523DF0/sub_425290
preserve EBX/EBP/ESI/EDI but our own state-management uses them.)
"""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    bot_char_tmp_va         = layout.va('bot_char_tmp')
    is_hitscan_va           = layout.va('is_hitscan')
    primary_hash_va         = layout.va('primary_hash')
    inv_tmp_va              = layout.va('inv_tmp')
    current_weapon_obj_va   = layout.va('current_weapon_obj')
    current_proto_va_va     = layout.va('current_proto_va')
    weapon_table_va         = layout.va('weapon_table')
    proj_speed_va           = layout.va('proj_speed')
    default_proj_speed_va   = layout.va('default_proj_speed')

    a.label('compute_proj_speed')

    # Reset to the fallback every call. Without this, if a previous frame
    # matched a weapon and overwrote proj_speed, switching to an unknown
    # weapon would leave the old per-weapon speed in place. The default lives
    # in default_proj_speed (immutable; written at build time).
    a.raw(b'\xA1' + le32(default_proj_speed_va))          # mov eax, [default_proj_speed]
    a.raw(b'\xA3' + le32(proj_speed_va))                  # mov [proj_speed], eax

    # Always clear the hitscan flag and the diagnostic stashes so a bailout
    # below leaves consistent state (apply_lead runs with the default speed).
    a.raw(b'\xC7\x05' + le32(is_hitscan_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(current_weapon_obj_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(current_proto_va_va) + le32(0))

    # --- (1) Load bot char; bail if NULL.
    a.raw(b'\x8B\x0D' + le32(bot_char_tmp_va))            # mov ecx, [bot_char_tmp]
    a.raw(b'\x85\xC9'); a.jz('cps_done')

    # --- (2) sub_4267E0(this = char) -> inventory.
    a.call_va(ax.SUB_4267E0_VA)
    a.raw(b'\x85\xC0'); a.jz('cps_done')                  # NULL inventory -> bail
    a.raw(b'\xA3' + le32(inv_tmp_va))                     # mov [inv_tmp], eax

    # --- (3) Lazy-init primary_hash. If already cached (non-zero), skip the
    # string lookup.
    a.raw(b'\x83\x3D' + le32(primary_hash_va) + b'\x00')  # cmp [primary_hash], 0
    a.jnz('cps_have_hash')
    # sub_523DF0(this = SLOT_NAME_REGISTRY, "Primary", -1) — __thiscall, ret 8.
    a.raw(b'\x6A\xFF')                                    # push -1
    a.raw(b'\x68' + le32(ax.PRIMARY_STR_VA))              # push "Primary"
    a.raw(b'\xB9' + le32(ax.SLOT_NAME_REGISTRY_VA))       # mov ecx, registry
    a.call_va(ax.SUB_523DF0_VA)
    a.raw(b'\xA3' + le32(primary_hash_va))                # mov [primary_hash], eax
    a.label('cps_have_hash')

    # --- (4) sub_425290(this = inv, hash) -> item id.   (__thiscall, ret 4)
    a.raw(b'\xFF\x35' + le32(primary_hash_va))            # push [primary_hash]
    a.raw(b'\x8B\x0D' + le32(inv_tmp_va))                 # mov ecx, [inv_tmp]
    a.call_va(ax.SUB_425290_VA)
    # EAX = item id (engine returns 0/-1-ish for missing slots; the virtual
    # call below still tolerates that since the engine itself does the same
    # sequence with no extra guard).

    # --- (5) inv.vtable[+0x68](this = inv, item) -> weapon obj.
    a.raw(b'\x50')                                        # push eax (item)
    a.raw(b'\x8B\x0D' + le32(inv_tmp_va))                 # mov ecx, [inv_tmp]
    a.raw(b'\x8B\x01')                                    # mov eax, [ecx] (vtable)
    a.raw(b'\xFF\x50' + bytes([ax.INVENTORY_GET_WEAPON_OFF]))  # call [eax+0x68]
    a.raw(b'\x85\xC0'); a.jz('cps_done')                  # NULL weapon -> bail
    a.raw(b'\xA3' + le32(current_weapon_obj_va))          # mov [current_weapon_obj], eax

    # --- (6) Read the weapon-class vtable at [weapon + 0x00]. This is the
    # stable per-class identifier (same value for every player holding the
    # same weapon). Kept in `current_proto_va` for diagnostic compatibility
    # with the existing weapon_info snapshot chunk.
    a.raw(b'\x8B\x10')                                    # mov edx, [eax]  (weapon vtable)
    a.raw(b'\x89\x15' + le32(current_proto_va_va))        # mov [current_proto_va], edx
    a.raw(b'\x85\xD2')                                    # test edx, edx
    a.jz('cps_done')                                      # NULL vtable shouldn't happen, bail safely

    # --- (7) Linear scan weapon_table for vtable VA in EDX. Each entry is
    # 8 bytes: (vtable_va u32, speed float). Zero key terminates. A matched
    # entry with speed == 0.0 means HITSCAN — set the flag and bail without
    # touching proj_speed (bot_fire_aim will skip apply_lead).
    a.raw(b'\xB8' + le32(weapon_table_va))                # mov eax, weapon_table
    a.label('cps_scan')
    a.raw(b'\x8B\x08')                                    # mov ecx, [eax]
    a.raw(b'\x85\xC9')                                    # test ecx, ecx
    a.jz('cps_done')                                      # sentinel -> no match
    a.raw(b'\x3B\xCA')                                    # cmp ecx, edx
    a.jz('cps_match')
    a.raw(b'\x83\xC0\x08')                                # add eax, 8
    a.jmp('cps_scan')

    a.label('cps_match')
    a.raw(b'\x8B\x48\x04')                                # mov ecx, [eax+4]  (speed bits)
    a.raw(b'\x85\xC9')                                    # test ecx, ecx
    a.jnz('cps_match_proj')
    # Hitscan sentinel (speed bits == 0 / float 0.0).
    a.raw(b'\xC7\x05' + le32(is_hitscan_va) + le32(1))
    a.jmp('cps_done')
    a.label('cps_match_proj')
    a.raw(b'\x89\x0D' + le32(proj_speed_va))              # mov [proj_speed], ecx

    a.label('cps_done')
    a.raw(b'\xC3')                                        # ret
