"""Free-function emit helpers for the .zaxbot hook payload.

Each helper takes the live ``Asm`` instance as its first argument and emits
inline at the current cursor.

- ``emit_logc_body`` / ``emit_wbuf_body`` define the shared body labels
  ('logc' / 'wbuf'); call them once from the entry module.
- ``emit_logc_call`` writes the byte and ``call``s the shared body.
- ``mp_gate`` walks mgr -> level -> mpd, jz-ing to ``fail_label`` on null;
  leaves mpd in EAX on success.
- ``enter_cs`` / ``leave_cs`` acquire/release the DirectPlay CritSec.
"""

from .. import addresses as ax
from ..asm import Asm, le32


def enter_cs(a: Asm) -> None:
    a.raw(b'\x68' + le32(ax.DP_CRITSECT_VA))
    a.raw(b'\xFF\x15' + le32(ax.IMP_ENTERCS))


def leave_cs(a: Asm) -> None:
    a.raw(b'\x68' + le32(ax.DP_CRITSECT_VA))
    a.raw(b'\xFF\x15' + le32(ax.IMP_LEAVECS))


def emit_logc_call(a: Asm, logbyte_va: int, ch: int) -> None:
    a.raw(b'\xC6\x05' + le32(logbyte_va) + bytes([ch]))
    a.call_lbl('logc')


def emit_logc_body(a: Asm, *, stepfn_va: int, dummy_va: int, logbyte_va: int) -> None:
    """One-letter progress marker -> zax_step.log; preserves all regs."""
    a.label('logc')
    a.raw(b'\x60')
    a.raw(b'\x6A\x00\x68' + le32(0x80) + b'\x6A\x04\x6A\x00\x6A\x03\x68'
          + le32(0x40000000) + b'\x68' + le32(stepfn_va))
    a.raw(b'\xFF\x15' + le32(ax.IMP_CREATEFILEA))
    a.raw(b'\x83\xF8\xFF'); a.jz('logc_done')
    a.raw(b'\x85\xC0'); a.jz('logc_done')
    a.raw(b'\x89\xC3')
    a.raw(b'\x6A\x02\x6A\x00\x6A\x00\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_SETFILEPTR))
    a.raw(b'\x6A\x00\x68' + le32(dummy_va) + b'\x6A\x01\x68' + le32(logbyte_va) + b'\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_WRITEFILE))
    a.raw(b'\x53'); a.raw(b'\xFF\x15' + le32(ax.IMP_CLOSEHANDLE))
    a.label('logc_done')
    a.raw(b'\x61'); a.raw(b'\xC3')


def emit_wbuf_body(a: Asm, *, dummy_va: int) -> None:
    """WriteFile(ebx=hFile, eax=buf, edx=len, &dummy, 0); preserves ebx."""
    a.label('wbuf')
    a.raw(b'\x6A\x00')
    a.raw(b'\x68' + le32(dummy_va))
    a.raw(b'\x52\x50\x53')
    a.raw(b'\xFF\x15' + le32(ax.IMP_WRITEFILE))
    a.raw(b'\xC3')


def mp_gate(a: Asm, fail_label: str) -> None:
    """Walk mgr -> level -> mpd; jz ``fail_label`` on any null. Leaves mpd in EAX.

    Emits the exact byte sequence used by the dispatcher's B-key handler, the
    R-key handler, and ``detect_mode``. The spawn flow's MP re-gate uses a
    slightly different register (EDX) and is intentionally NOT factored here.
    """
    a.raw(b'\xA1' + le32(ax.MANAGER_GLOBAL_VA))
    a.raw(b'\x85\xC0'); a.jz(fail_label)
    a.raw(b'\x89\xC1'); a.raw(b'\x8B\x10')
    a.raw(b'\xFF\x92' + le32(ax.VT_OFFSET_TO_LVL))
    a.raw(b'\x85\xC0'); a.jz(fail_label)
    a.raw(b'\x8B\x40' + bytes([ax.MP_DATA_FIELD]))
    a.raw(b'\x85\xC0'); a.jz(fail_label)
