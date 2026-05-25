"""Hook payload assembly for the .zaxbot section.

Sub-modules emit ordered slices of the payload into a shared ``Asm`` instance.
``entry.build_hook`` orchestrates them, then returns the linked section bytes
plus the label-VA dictionary that ``patch_manifest`` patches sites to."""
