"""Top-level helpers for building a patched PE image."""

from dataclasses import dataclass

from .patch_manifest import apply_patches
from .pe import PEImage


@dataclass(frozen=True)
class SectionSpec:
    name: bytes
    rva: int
    size: int
    characteristics: int
    raw_size: int | None = None


@dataclass(frozen=True)
class PatchedImage:
    data: bytes
    info: dict
    raw_off: int
    applied: dict
    section_va_abs: int


def build_patched_image(source_path, image_base, section, build_section, patches):
    with open(source_path, 'rb') as f:
        data = bytearray(f.read())
    pe = PEImage(data, image_base)

    section_va_abs = image_base + section.rva
    section_bytes, info = build_section(section_va_abs)
    raw_size = section.size if section.raw_size is None else section.raw_size
    raw_off = pe.append_section(
        section.name,
        section.rva,
        section.size,
        raw_size,
        section.characteristics,
        section_bytes,
    )

    applied = apply_patches(pe, patches, info)
    return PatchedImage(
        data=bytes(pe.data),
        info=info,
        raw_off=raw_off,
        applied=applied,
        section_va_abs=section_va_abs,
    )

