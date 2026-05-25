"""``detect_mode`` body: best-effort mode discovery + one-shot mpd dump.

Earlier vtable-scan attempts crashed on a 4-byte window over an internal
string inside ``mpd`` (the value ``0x6E6F6E00`` — "non\\0" — passed our range
check but pointed at unmapped memory). Safe v1.1: no unsafe deref. We dump
``mpd[0..0x200]`` once per session so the right offset can be discovered
offline, and return 0 (DM default).

Caller has already validated MP via the dispatcher's MP-gate; we re-walk the
chain here to acquire mpd locally."""

from .. import addresses as ax
from ..asm import Asm, le32
from ..layout import ScratchLayout
from .helpers import mp_gate


def emit(a: Asm, layout: ScratchLayout) -> None:
    diag_dumped_va = layout.va('diag_dumped')
    hdr_va         = layout.va('hdr')
    fn_va          = layout.va('fn')

    a.label('detect_mode')
    mp_gate(a, 'dm_fallback')                              # mgr -> level -> mpd; mpd in eax
    a.raw(b'\x89\xC1')                                      # mov ecx, eax  (mpd for dump)

    a.raw(b'\x83\x3D' + le32(diag_dumped_va) + b'\x00')
    a.jnz('dm_fallback')
    a.raw(b'\xC7\x05' + le32(diag_dumped_va) + le32(1))    # mark dumped (one-shot)
    a.raw(b'\x89\x0D' + le32(hdr_va))                       # [hdr] = mpd (ecx)
    a.raw(b'\xC7\x05' + le32(hdr_va + 4) + le32(0x200))    # [hdr+4] = 0x200
    a.raw(b'\x6A\x00\x68' + le32(0x80) + b'\x6A\x04\x6A\x00\x6A\x03\x68'
          + le32(0x40000000) + b'\x68' + le32(fn_va))
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEFILEA))
    a.raw(b'\x83\xF8\xFF'); a.jz('dm_fallback')
    a.raw(b'\x85\xC0'); a.jz('dm_fallback')
    a.raw(b'\x89\xC3')                                      # mov ebx, eax (hFile)
    a.raw(b'\x6A\x02\x6A\x00\x6A\x00\x53')                 # SetFilePointer(h, 0, NULL, FILE_END=2)
    a.raw(b'\xFF\x15' + le32(ax.IMP_SETFILEPTR))
    a.raw(b'\xB8' + le32(hdr_va))                           # eax = hdr
    a.raw(b'\xBA\x08\x00\x00\x00'); a.call_lbl('wbuf')      # write 8-byte header
    a.raw(b'\xA1' + le32(hdr_va))                           # eax = mpd
    a.raw(b'\xBA\x00\x02\x00\x00'); a.call_lbl('wbuf')      # write 0x200 bytes
    a.raw(b'\x53'); a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))

    a.label('dm_fallback')
    a.raw(b'\x31\xC0\xC3')                                  # xor eax,eax; ret  (mode 0 = DM)
