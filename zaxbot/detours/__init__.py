"""Detour bodies emitted into the .zaxbot section.

Each module exposes ``emit(a, layout)``. The entry orchestrator calls them in
a fixed order; the resulting label VAs become ``detour_*_va`` entries in the
``info`` dict that ``patch_manifest`` uses to wire up redirect sites in the
original PE."""
