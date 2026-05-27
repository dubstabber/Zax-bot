"""``compute_proj_speed`` — per-fire-frame weapon dispatch for shot leading.

Replicates the engine's own weapon-lookup chain from ``sub_543830``:

    inv  = sub_4267E0(this = bot_char)                ; inventory
    hash = sub_523DF0(this = SLOT_NAME_REGISTRY,
                      "Primary", -1)                  ; slot hash
    item = sub_425290(this = inv, hash)               ; item id
    wpn  = inv.vtable[+0x68](this = inv, item)        ; weapon object

…then calls ``sub_4DD480(wpn)`` to read the inventory-item definition pointer.
The generic item vtable at ``[wpn + 0x00]`` is shared across weapons, so the
definition pointer is the stable key.

Three-tier dispatch:

  1. **Manual override**: a linear scan over ``weapon_table`` (built from
     ``cfg.WEAPON_SPEEDS`` at patch time). On a hit, the override wins;
     speed = 0.0 means "force hitscan even if the engine has a prototype".
  2. **Dynamic def read**: on no override match, read ``[def + 0x20]``
     ("Projectiles/Projectile"). NULL ⇒ weapon is hitscan (Semi Auto Pistol,
     Alien Electrical Weapon, etc.) — set ``is_hitscan`` and bail.
     Non-NULL ⇒ pointer to a CModel projectile prototype; read
     ``[proto + 0x60]`` ("Move/Max Velocity", float, pixels/sec) and
     multiply by ``speed_scale`` to land in per-call units.
  3. **Static fallback**: ``proj_speed`` was reset to ``default_proj_speed``
     at function entry, so any bail-out (NULL weapon, NULL def, etc.)
     leaves the old ``cfg.PROJECTILE_SPEED`` behavior intact.

The ``primary_hash`` value is process-stable, so it's cached on first use
and reused thereafter — only one call per process pays for the string
lookup. Every other field touched here (``inv_tmp``, ``current_weapon_obj``,
``current_proto_va``, ``current_proto_model_va``, ``proto_speed_raw``,
``is_hitscan``, ``proj_speed``) is per-call scratch.

Diagnostic stashes feed the widened ``weapon_info`` snapshot chunk so the
user can identify each weapon's item definition pointer, see what the
engine's def-field read returned, and tune ``cfg.SPEED_SCALE`` by pressing R
in-game.

Inputs (scratch):
  ``bot_char_tmp`` — bot's char ptr (set by detour_5436F0).
  ``weapon_table`` — packed (item_def_va, speed) entries + 0-VA sentinel.
  ``speed_scale``  — build-time const, multiplier for raw pixels/sec speed.

Outputs (scratch):
  ``is_hitscan``           — 1 if weapon has no projectile (skip apply_lead).
  ``proj_speed``           — projectile speed for apply_lead this frame.
  ``current_weapon_obj``   — weapon obj ptr (diagnostic).
  ``current_proto_va``     — inventory item-definition pointer (diagnostic).
  ``current_proto_model_va``— [def+0x20] projectile prototype, 0 if hitscan (diagnostic).
  ``proto_speed_raw``      — [proto+0x60] raw pixels/sec from def (diagnostic).
  ``primary_hash``         — cached "Primary" slot hash (lazy init).
  ``inv_tmp``              — inventory ptr held across sub_523DF0 call.

Side effects: 5 engine calls per fire frame per bot (sub_4267E0, possibly
sub_523DF0 on first call only, sub_425290, inv.vtable[+0x68], sub_4DD480). Same chain
sub_543830 itself runs, so the net engine overhead roughly doubles for
those four targets — acceptable. The def-read tail adds one more engine
call (sub_48D8F0 to resolve the projectile registry key to a CModel*),
plus a pointer deref and an FPU mul.

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
    current_proto_model_va_va = layout.va('current_proto_model_va')
    proto_speed_raw_va      = layout.va('proto_speed_raw')
    speed_scale_va          = layout.va('speed_scale')
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
    a.raw(b'\xC7\x05' + le32(current_proto_model_va_va) + le32(0))
    a.raw(b'\xC7\x05' + le32(proto_speed_raw_va) + le32(0))

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

    # --- (6) Read the inventory item-definition pointer. This is the stable
    # per-weapon key; [weapon+0] is only the generic CInventoryItem vtable.
    a.raw(b'\x8B\xC8')                                    # mov ecx, eax (weapon)
    a.call_va(ax.SUB_4DD480_VA)                           # eax = item definition
    a.raw(b'\x89\x05' + le32(current_proto_va_va))        # mov [current_proto_va], eax
    a.raw(b'\x85\xC0')                                    # test eax, eax
    a.jz('cps_done')
    a.raw(b'\x8B\xD0')                                    # mov edx, eax (table key)

    # --- (7) Linear scan weapon_table for item-definition VA in EDX. Each entry is
    # 8 bytes: (definition_va u32, speed float). Zero key terminates. A matched
    # entry with speed == 0.0 means HITSCAN — set the flag and bail without
    # touching proj_speed (bot_fire_aim will skip apply_lead). On no match
    # (sentinel reached), fall through to the def-field read at
    # cps_try_def_field so unconfigured weapons still get a correct speed.
    a.raw(b'\xB8' + le32(weapon_table_va))                # mov eax, weapon_table
    a.label('cps_scan')
    a.raw(b'\x8B\x08')                                    # mov ecx, [eax]
    a.raw(b'\x85\xC9')                                    # test ecx, ecx
    a.jz('cps_try_def_field')                             # sentinel -> try engine def
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
    a.jmp('cps_done')

    # --- (8) Dynamic def-field read: no manual override hit. EDX still holds
    # the def_va from step (6). The "Projectiles/Projectile" field at
    # PROJ_PROTO_OFF is NOT a resolved CModel pointer — it's the integer
    # registry key stored by sub_54E560's "registry reference" field type.
    # The engine resolves it lazily via sub_48D8F0(MODEL_REGISTRY, key) ->
    # CModel*, the same resolver the force-equip path uses for the item-def
    # registry. Pattern verified at sub_489A40:0x489a57 (`sub_48D8F0(
    # dword_6CFDD8, *(this + 29))`).
    #
    # Hitscan detection: key == 0 means the def didn't define a projectile
    # reference (schema default is 0 — see the 7th arg to sub_54E560 in
    # sub_4D5620). Velocity == 0 after resolution covers the edge case
    # where the def references the "none" model.
    a.label('cps_try_def_field')
    a.raw(b'\x8B\x4A' + bytes([ax.PROJ_PROTO_OFF]))       # mov ecx, [edx + 0x20] — raw registry key
    a.raw(b'\x85\xC9')                                    # test ecx, ecx
    a.jz('cps_hitscan_from_def')                          # key == 0 -> no projectile assigned

    # Resolve key -> CModel* via sub_48D8F0(this=MODEL_REGISTRY_VA, key).
    a.raw(b'\x51')                                        # push ecx (key)
    a.raw(b'\xB9' + le32(ax.MODEL_REGISTRY_VA))           # mov ecx, MODEL_REGISTRY_VA
    a.call_va(ax.SUB_48D8F0_VA)                           # eax = CModel* (or 0)
    a.raw(b'\xA3' + le32(current_proto_model_va_va))      # mov [current_proto_model_va], eax
    a.raw(b'\x85\xC0')                                    # test eax, eax
    a.jz('cps_done')                                      # resolver returned NULL -> keep default speed

    # Read [CModel + 0x60] (Move/Max Velocity, float pixels/sec). Test the
    # int bits first (+0.0 == 0x00000000) so we don't need FPU stack juggling
    # in the hitscan branch.
    a.raw(b'\x8B\x48' + bytes([ax.MODEL_MAX_VEL_OFF]))    # mov ecx, [eax + 0x60]
    a.raw(b'\x89\x0D' + le32(proto_speed_raw_va))         # mov [proto_speed_raw], ecx
    a.raw(b'\x85\xC9')                                    # test ecx, ecx
    a.jz('cps_hitscan_from_def')                          # speed == 0.0 -> hitscan-equivalent

    a.raw(b'\xD9\x40' + bytes([ax.MODEL_MAX_VEL_OFF]))    # fld dword [eax + 0x60]
    a.raw(b'\xD8\x0D' + le32(speed_scale_va))             # fmul dword [speed_scale]
    a.raw(b'\xD9\x1D' + le32(proj_speed_va))              # fstp dword [proj_speed]
    a.jmp('cps_done')

    a.label('cps_hitscan_from_def')
    # No projectile (key=0 or velocity=0) — engine treats this weapon as
    # hitscan. Leave proj_speed at default_proj_speed; apply_lead is skipped.
    a.raw(b'\xC7\x05' + le32(is_hitscan_va) + le32(1))

    a.label('cps_done')
    a.raw(b'\xC3')                                        # ret
