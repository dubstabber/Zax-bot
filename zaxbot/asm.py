"""Small x86 encoding helpers used by the patch payload builder."""

import struct


def le32(v):
    return struct.pack('<I', v & 0xFFFFFFFF)


def le32s(v):
    return struct.pack('<i', v)


def rel32_from(src_va, instruction_len, target_va):
    return target_va - (src_va + instruction_len)


def encode_rel32_from(src_va, instruction_len, target_va):
    return le32s(rel32_from(src_va, instruction_len, target_va))


class Asm:
    """Tiny label-based x86 assembler for the hook payload.

    All control-flow uses near (rel32) forms so we never worry about rel8 range.
    `base_va` is the absolute VA the emitted buffer will load at.
    """

    def __init__(self, base_va):
        self.base_va = base_va
        self.buf = bytearray()
        self.labels = {}
        # fixups: (pos, kind, target)  kind in {'lbl','va'}; rel32 patched at link()
        self.fixups = []

    def raw(self, b):
        self.buf += bytes(b)

    def label(self, name):
        assert name not in self.labels
        self.labels[name] = len(self.buf)

    def _rel32_fixup(self, target, kind):
        self.fixups.append((len(self.buf), kind, target))
        self.buf += b'\x00\x00\x00\x00'

    def jz(self, label):      # 0F 84 rel32
        self.raw(b'\x0F\x84')
        self._rel32_fixup(label, 'lbl')

    def jnz(self, label):     # 0F 85 rel32
        self.raw(b'\x0F\x85')
        self._rel32_fixup(label, 'lbl')

    def jb(self, label):      # 0F 82 rel32
        self.raw(b'\x0F\x82')
        self._rel32_fixup(label, 'lbl')

    def jae(self, label):     # 0F 83 rel32 (unsigned >=)
        self.raw(b'\x0F\x83')
        self._rel32_fixup(label, 'lbl')

    def ja(self, label):      # 0F 87 rel32 (unsigned >)
        self.raw(b'\x0F\x87')
        self._rel32_fixup(label, 'lbl')

    def jbe(self, label):     # 0F 86 rel32 (unsigned <=)
        self.raw(b'\x0F\x86')
        self._rel32_fixup(label, 'lbl')

    def jge(self, label):     # 0F 8D rel32 (signed >=)
        self.raw(b'\x0F\x8D')
        self._rel32_fixup(label, 'lbl')

    def jl(self, label):      # 0F 8C rel32 (signed <)
        self.raw(b'\x0F\x8C')
        self._rel32_fixup(label, 'lbl')

    def jmp(self, label):     # E9 rel32
        self.raw(b'\xE9')
        self._rel32_fixup(label, 'lbl')

    def call_lbl(self, label):  # E8 rel32 -> label
        self.raw(b'\xE8')
        self._rel32_fixup(label, 'lbl')

    def jmp_va(self, va):     # E9 rel32 -> absolute va
        self.raw(b'\xE9')
        self._rel32_fixup(va, 'va')

    def call_va(self, va):    # E8 rel32 -> absolute va
        self.raw(b'\xE8')
        self._rel32_fixup(va, 'va')

    def imm32_lbl(self, label):
        """Emit a 4-byte placeholder fixed up to label's absolute VA."""
        self.fixups.append((len(self.buf), 'abs', label))
        self.buf += b'\x00\x00\x00\x00'

    def link(self):
        for pos, kind, target in self.fixups:
            if kind == 'abs':
                self.buf[pos:pos + 4] = le32(self.base_va + self.labels[target])
                continue
            tgt_va = self.base_va + self.labels[target] if kind == 'lbl' else target
            rel = tgt_va - (self.base_va + pos + 4)
            self.buf[pos:pos + 4] = le32s(rel)
        return bytes(self.buf)

