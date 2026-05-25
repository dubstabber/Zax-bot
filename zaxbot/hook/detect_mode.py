"""``detect_mode``: ask the engine for the active game-type and classify it.

`sub_59FF90(ecx=mgr)` is the engine's own "active game-type instance" getter
(found via `sub_5BAD10` which uses it to emit the `"gametype"` property
string). It returns a pointer to the active CMultiPlayerGameType-derived
instance — `CDeathMatchGameType`, `CCaptureTheFlagGameType`,
`CSalvageKingGameType`, etc. — or NULL outside a live game. `[result + 0]`
is that instance's vtable, which is one of `VT_DM_VA` / `VT_CTF_VA` /
`VT_SK_VA`. Reading that vtable and comparing against the three known VAs
gives a clean mode classification with no scanning and no risky deref
chain.

`mpd` (`[level + 0x30]`) was the wrong target — it's the 24-byte
`CMultiPlayerGameData` *base* allocated by `sub_51C010` with the shared
vtable `0x5FB104`; that vtable doesn't distinguish modes. See the
``mode-detection-mpd-pitfall`` memory for the misdiagnosis history.

`FORCE_MODE` (in `zaxbot/config.py`) short-circuits this whole flow when
set to `'dm'`/`'ctf'`/`'sk'`. Default is auto-detect."""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout


def emit(a: Asm, layout: ScratchLayout) -> None:
    diag_dumped_va = layout.va('diag_dumped')
    hdr_va         = layout.va('hdr')
    fn_va          = layout.va('fn')
    forced_mode_va = layout.va('forced_mode')

    a.label('detect_mode')

    # FORCE_MODE override: 0xFFFFFFFF = auto-detect; 0/1/2 = DM/CTF/SK.
    a.raw(b'\xA1' + le32(forced_mode_va))                   # mov eax, [forced_mode]
    a.raw(b'\x83\xF8\xFF')                                  # cmp eax, -1
    a.jnz('ret_forced')

    # Load the game/world manager pointer.
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))             # mov eax, [mgr]
    a.raw(b'\x85\xC0'); a.jz('dm_fallback')

    # Ask the engine for the active game-type instance.
    # __usercall(this=ecx, hint=esi) -> eax. ESI is only consulted on a
    # cache-miss during resource load; during gameplay the resource is
    # already cached, so we zero ESI to keep stack garbage out of the
    # cache-miss path even if it ever fires.
    a.raw(b'\x89\xC1')                                      # mov ecx, eax  (mgr)
    a.raw(b'\x31\xF6')                                      # xor esi, esi
    a.call_va(ax.SUB_59FF90_VA)                             # eax = gametype or NULL
    a.raw(b'\x85\xC0'); a.jz('dm_fallback')

    # Read the game-type vtable and dispatch on the three known VAs.
    a.raw(b'\x8B\x08')                                      # mov ecx, [eax]  (vtable)
    a.raw(b'\x81\xF9' + le32(ax.VT_DM_VA))                  # cmp ecx, VT_DM_VA
    a.jz('dm_fallback')                                     # DM -> return 0 (no dump)
    a.raw(b'\x81\xF9' + le32(ax.VT_CTF_VA))                 # cmp ecx, VT_CTF_VA
    a.jz('ret_ctf')
    a.raw(b'\x81\xF9' + le32(ax.VT_SK_VA))                  # cmp ecx, VT_SK_VA
    a.jz('ret_sk')

    # Unknown vtable: one-shot dump of [eax..eax+0x200] for offline analysis
    # (e.g. quest-mode or a build variant), then fall back to DM.
    a.raw(b'\x89\xC1')                                      # mov ecx, eax  (gametype for dump)
    a.raw(b'\x83\x3D' + le32(diag_dumped_va) + b'\x00')
    a.jnz('dm_fallback')
    a.raw(b'\xC7\x05' + le32(diag_dumped_va) + le32(1))     # mark dumped (one-shot)
    a.raw(b'\x89\x0D' + le32(hdr_va))                       # [hdr] = gametype (ecx)
    a.raw(b'\xC7\x05' + le32(hdr_va + 4) + le32(0x200))     # [hdr+4] = 0x200
    a.raw(b'\x6A\x00\x68' + le32(0x80) + b'\x6A\x04\x6A\x00\x6A\x03\x68'
          + le32(0x40000000) + b'\x68' + le32(fn_va))
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEFILEA))
    a.raw(b'\x83\xF8\xFF'); a.jz('dm_fallback')
    a.raw(b'\x85\xC0'); a.jz('dm_fallback')
    a.raw(b'\x89\xC3')                                      # mov ebx, eax (hFile)
    a.raw(b'\x6A\x02\x6A\x00\x6A\x00\x53')                  # SetFilePointer(h, 0, NULL, FILE_END=2)
    a.raw(b'\xFF\x15' + le32(ax.IMP_SETFILEPTR))
    a.raw(b'\xB8' + le32(hdr_va))                           # eax = hdr
    a.raw(b'\xBA\x08\x00\x00\x00'); a.call_lbl('wbuf')      # write 8-byte header
    a.raw(b'\xA1' + le32(hdr_va))                           # eax = gametype
    a.raw(b'\xBA\x00\x02\x00\x00'); a.call_lbl('wbuf')      # write 0x200 bytes
    a.raw(b'\x53'); a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))

    a.label('dm_fallback')
    a.raw(b'\x31\xC0\xC3')                                  # xor eax,eax; ret  (mode 0 = DM)

    a.label('ret_ctf')
    a.raw(b'\xB8\x01\x00\x00\x00\xC3')                      # mov eax, 1; ret   (mode 1 = CTF)

    a.label('ret_sk')
    a.raw(b'\xB8\x02\x00\x00\x00\xC3')                      # mov eax, 2; ret   (mode 2 = SK)

    a.label('ret_forced')
    a.raw(b'\xC3')                                          # ret  (EAX = forced_mode value)
