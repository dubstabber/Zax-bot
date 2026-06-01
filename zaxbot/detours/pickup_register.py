"""``detour_53DA40`` — per-pickup self-registration (item-grab feature, stage 1).

``sub_53DA40`` is the CPickupAI per-frame update: the engine calls it once per
pickup ENTITY every frame (it manages the pickup's respawn timer off game-time
deltas). Pickups are otherwise not enumerable — they are CPickupAI grid
components, absent from every flat array (``mgr+0x290`` is players, ``mgr+0x2BC``
is layers), and the engine's spatial query is masked to blocking entities only.
So instead of scanning *for* pickups, we let each pickup register *itself* here.

## Why we re-run the prologue first

The pickup entity is whatever the engine loads into EBX via the prologue's own
``mov ebx, [esp+0x30]`` (it then does ``sub_4FB0A0(ebx, …)`` and reads
``ebx[7]`` flags). Rather than re-derive which stack slot that is — and risk an
off-by-4 — the detour re-executes the exact 8-byte prologue
(``sub esp,24h; push ebx; mov ebx,[esp+0x30]``) so EBX holds the entity exactly
as the engine computes it, with ESP adjusted precisely as RESUME (0x53DA48)
expects. Registration then reads EBX, and a ``pushad``/``pushfd`` pair keeps
every register and flag intact for the engine code that resumes after.

## Per-frame table reset

``world_frame`` is incremented once per frame by the page-flip detour
(``detour_5693A0``). The first registration of each frame sees ``world_frame !=
pickup_last_frame`` and clears ``pickup_count`` before appending; later pickups
that frame just append. So the table holds exactly one frame's pickups and any
reader (the overlay now; the bot item-divert AI later) never sees a half-built
list, regardless of update ordering within the frame.

Output convention: appends ``(x, y)`` float pairs to ``pickup_table`` and
maintains ``pickup_count``; reads nothing the engine path depends on.
"""

from .. import addresses as ax
from .. import config as cfg
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    enabled_va     = layout.va('pickup_register_enabled')
    world_frame_va = layout.va('world_frame')
    last_frame_va  = layout.va('pickup_last_frame')
    count_va       = layout.va('pickup_count')
    tmp_va         = layout.va('pickup_reg_tmp')
    table_va       = layout.va('pickup_table')
    table_max      = cfg.PICKUP_TABLE_MAX

    a.label('detour_53DA40')
    # Re-execute the displaced prologue: EBX = pickup entity (engine's own
    # computation), ESP adjusted exactly as the code at RESUME expects.
    a.raw(ax.S53DA40_PROLOGUE)                            # sub esp,24h; push ebx; mov ebx,[esp+0x30]

    a.raw(b'\x60')                                        # pushad
    a.raw(b'\x9C')                                        # pushfd

    a.raw(b'\x83\x3D' + le32(enabled_va) + b'\x00')       # cmp [pickup_register_enabled], 0
    a.jz('pr_done')
    a.raw(b'\x85\xDB'); a.jz('pr_done')                   # entity (ebx) NULL?
    # Heap-range sanity: entities live in Wine's userland heap, not the PE
    # image — mirrors the snapshot/world_scan range guards.
    a.raw(b'\x81\xFB\x00\x00\x40\x00')                    # cmp ebx, 0x00400000
    a.jb('pr_done')
    a.raw(b'\x81\xFB\x00\x00\x00\x70')                    # cmp ebx, 0x70000000
    a.jae('pr_done')

    # Lazy per-frame reset: if world_frame changed since the last reset, the
    # table is stale from a previous frame — clear the count before appending.
    a.raw(b'\xA1' + le32(world_frame_va))                 # eax = world_frame
    a.raw(b'\x3B\x05' + le32(last_frame_va))              # cmp eax, [pickup_last_frame]
    a.jz('pr_no_reset')
    a.raw(b'\xA3' + le32(last_frame_va))                  # pickup_last_frame = world_frame
    a.raw(b'\xC7\x05' + le32(count_va) + le32(0))         # pickup_count = 0
    a.label('pr_no_reset')

    # Collectible filter: only register pickups that are currently PRESENT.
    # Respawning spawners keep ticking after collection with the present-bits
    # cleared, so without this their markers persist on an empty pad. The
    # entity flags live at +0x1C; require (flags & MASK) == VALUE. Placed AFTER
    # the per-frame reset so the table still clears each frame even when every
    # pickup is currently collected (-> empty table -> no stale markers). Mask
    # 0 disables the filter (see cfg.PICKUP_ACTIVE_MASK).
    if cfg.PICKUP_ACTIVE_MASK:
        a.raw(b'\x8B\x43\x1C')                            # mov eax, [ebx+0x1C] (entity flags)
        a.raw(b'\x25' + le32(cfg.PICKUP_ACTIVE_MASK))     # and eax, MASK
        a.raw(b'\x3D' + le32(cfg.PICKUP_ACTIVE_VALUE))    # cmp eax, VALUE
        a.jnz('pr_done')

    # Bail if the table is full this frame.
    a.raw(b'\xA1' + le32(count_va))                       # eax = pickup_count
    a.raw(b'\x3D' + le32(table_max))                      # cmp eax, table_max
    a.jae('pr_done')

    # Read the entity's world position: sub_4FB0A0(ecx=entity, &pickup_reg_tmp).
    # __thiscall, ret 4 (pops the pushed &out). EBX (entity) is callee-saved
    # so it survives the call.
    a.raw(b'\x68' + le32(tmp_va))                         # push &pickup_reg_tmp
    a.raw(b'\x89\xD9')                                    # mov ecx, ebx (this = entity)
    a.call_va(ax.SUB_4FB0A0_VA)

    # Append (x, y) to pickup_table[pickup_count]; ++pickup_count.
    a.raw(b'\xA1' + le32(count_va))                       # eax = pickup_count
    a.raw(b'\x8D\x14\xC5' + le32(table_va))               # lea edx, [eax*8 + pickup_table]
    a.raw(b'\x8B\x0D' + le32(tmp_va))                     # ecx = x bits
    a.raw(b'\x89\x0A')                                    # [edx]   = x
    a.raw(b'\x8B\x0D' + le32(tmp_va + 4))                 # ecx = y bits
    a.raw(b'\x89\x4A\x04')                                # [edx+4] = y
    a.raw(b'\xFF\x05' + le32(count_va))                   # ++pickup_count

    a.label('pr_done')
    a.raw(b'\x9D')                                        # popfd
    a.raw(b'\x61')                                        # popad
    a.jmp_va(ax.S53DA40_RESUME)                           # resume at 0x53DA48 (test ebx, ebx)
