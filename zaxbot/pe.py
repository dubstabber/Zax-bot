"""PE image helpers for deterministic binary patching."""

from dataclasses import dataclass
import struct

from .asm import encode_rel32_from


def align_up(value, alignment):
    return ((value + alignment - 1) // alignment) * alignment


@dataclass(frozen=True)
class Section:
    name: bytes
    virtual_size: int
    virtual_address: int
    raw_size: int
    raw_pointer: int


class PEImage:
    def __init__(self, data, image_base):
        self.data = data
        self.image_base = image_base
        self._parse_headers()

    def _parse_headers(self):
        self.e_lfanew = struct.unpack_from('<I', self.data, 0x3C)[0]
        self.file_header_off = self.e_lfanew + 4
        self.nsec_off = self.file_header_off + 2
        self.nsec = struct.unpack_from('<H', self.data, self.nsec_off)[0]
        self.sizeof_optional_header = struct.unpack_from(
            '<H', self.data, self.file_header_off + 16
        )[0]
        self.optional_header_off = self.file_header_off + 20
        self.section_table_off = self.optional_header_off + self.sizeof_optional_header
        self.section_alignment = struct.unpack_from('<I', self.data, self.optional_header_off + 32)[0]
        self.file_alignment = struct.unpack_from('<I', self.data, self.optional_header_off + 36)[0]
        self.size_of_image_off = self.optional_header_off + 56
        self.size_of_headers = struct.unpack_from('<I', self.data, self.optional_header_off + 60)[0]
        self.sections = self._read_sections()

    def _read_sections(self):
        sections = []
        for i in range(self.nsec):
            off = self.section_table_off + i * 40
            sections.append(Section(
                name=bytes(self.data[off:off + 8]).rstrip(b'\x00'),
                virtual_size=struct.unpack_from('<I', self.data, off + 8)[0],
                virtual_address=struct.unpack_from('<I', self.data, off + 12)[0],
                raw_size=struct.unpack_from('<I', self.data, off + 16)[0],
                raw_pointer=struct.unpack_from('<I', self.data, off + 20)[0],
            ))
        return sections

    def va_to_offset(self, va):
        rva = va - self.image_base
        for sec in self.sections:
            span = max(sec.virtual_size, sec.raw_size)
            if sec.virtual_address <= rva < sec.virtual_address + span:
                return sec.raw_pointer + (rva - sec.virtual_address)
        raise ValueError(f'VA 0x{va:x} is outside mapped sections')

    def expect(self, va, expected):
        off = self.va_to_offset(va)
        actual = bytes(self.data[off:off + len(expected)])
        if actual != expected:
            raise AssertionError(
                f'bytes at 0x{va:x} unexpected: {actual.hex()} != {expected.hex()}'
            )
        return off

    def write_at_va(self, va, blob, expected=None):
        off = self.va_to_offset(va)
        if expected is not None:
            self.expect(va, expected)
        self.data[off:off + len(blob)] = blob
        return off

    def patch_call(self, va, target_va, expected=None):
        blob = b'\xE8' + encode_rel32_from(va, 5, target_va)
        self.write_at_va(va, blob, expected)
        return blob

    def patch_jmp(self, va, target_va, length, expected=None):
        if length < 5:
            raise ValueError('near JMP patch needs at least 5 bytes')
        blob = b'\xE9' + encode_rel32_from(va, 5, target_va) + b'\x90' * (length - 5)
        self.write_at_va(va, blob, expected)
        return blob

    def append_section(self, name, rva, virtual_size, raw_size, characteristics, section_bytes):
        new_section_hdr_off = self.section_table_off + self.nsec * 40
        if new_section_hdr_off + 40 > self.size_of_headers:
            raise SystemExit('not enough room in PE header for a new section')
        if len(section_bytes) != raw_size:
            raise ValueError(
                f'section payload size mismatch: 0x{len(section_bytes):x} != 0x{raw_size:x}'
            )

        raw_off = len(self.data)
        if raw_off % self.file_alignment != 0:
            raise AssertionError(f'file end not aligned: 0x{raw_off:x}')
        self.data += section_bytes

        hdr = bytearray(40)
        hdr[0:8] = name[:8].ljust(8, b'\x00')
        struct.pack_into('<I', hdr, 8, virtual_size)
        struct.pack_into('<I', hdr, 12, rva)
        struct.pack_into('<I', hdr, 16, raw_size)
        struct.pack_into('<I', hdr, 20, raw_off)
        struct.pack_into('<I', hdr, 36, characteristics)
        self.data[new_section_hdr_off:new_section_hdr_off + 40] = hdr

        struct.pack_into('<H', self.data, self.nsec_off, self.nsec + 1)
        new_size_of_image = align_up(rva + virtual_size, self.section_alignment)
        struct.pack_into('<I', self.data, self.size_of_image_off, new_size_of_image)

        self._parse_headers()
        return raw_off


@dataclass(frozen=True)
class RelocationPatch:
    name: str
    kind: str
    va: int
    original: bytes
    target_key: str
    length: int = 5

    def apply(self, image, targets):
        target_va = targets[self.target_key]
        if self.kind == 'call':
            return image.patch_call(self.va, target_va, self.original)
        if self.kind == 'jmp':
            return image.patch_jmp(self.va, target_va, self.length, self.original)
        raise ValueError(f'unknown patch kind: {self.kind}')


@dataclass(frozen=True)
class RawBytePatch:
    """Overwrites a fixed VA with `replacement` after verifying `original`.

    Used for in-place engine patches that don't redirect into .zaxbot — e.g.
    surgical fixes that NULL-guard a virtual-call site by writing a few extra
    bytes into the function's trailing padding.
    """
    name: str
    va: int
    original: bytes
    replacement: bytes

    def apply(self, image, targets):  # `targets` unused — keeps apply_patches uniform.
        image.write_at_va(self.va, self.replacement, self.original)
        return self.replacement
