# Repository Cleanup Notes (2025-10-14)

This cleanup removes outdated or contradictory documents and obsolete extraction outputs, keeping only the current, engine-aligned artifacts and specifications.

## Documentation changes

Removed (outdated/contradictory or superseded by current specs):
- docs/FINDINGS.md – early, now-incorrect summary (claimed 3D rendering, SEQ16 not implemented)
- docs/IDA_FRM_ANALYSIS.md – speculative FRM variants; superseded by FRM16_FORMAT.md
- docs/SEQ16_EXTRACTION_SUMMARY.md – redundant with SEQ16_FORMAT.md
- docs/EXTRACTION_COMPLETE.md – Godot usage guide tied to obsolete output layout
- docs/ISSUES_AND_FIXES.md – based on earlier header assumptions
- docs/MISSING_ASSETS_SUMMARY.md – speculative plan about .frm/.seq “source” formats
- docs/MDL_FORMAT.md – claimed MDL = 3D; contradicted by current IDA analysis (MDL = 2D POLY2D)

Updated:
- README.md – corrected Game Architecture (pre-rendered 2D sprites; MDL = 2D polygons) and docs links; table updated for MDL/SEQ16
- docs/SEQ16_FORMAT.md – added engine-confirmed “Playback Timing and FPS Flow” tying header FPS → Sequence_GetRate (0x4A5980) → tick (0x517F20)
- docs/IDA_PRO_ANALYSIS.md – documents concrete vtable address entries and GetRate implementation

Authoritative specs to use going forward:
- docs/FRM16_FORMAT.md – FRM16 texture format
- docs/SEQ16_FORMAT.md – SEQ16 animation format + timing
- docs/IDA_PRO_ANALYSIS.md – reverse engineering notes (MDL = 2D POLY2D; SEQ16 pipeline; vtable/addresses)

## Extracted assets changes

Removed (requested cleanup of obsolete/temporary outputs):
- `extracted/seq16/` - superseded by `extracted/animations/`
- `zax-assets/seq16_frames/` - superseded by direct sequence extraction under `extracted/animations/`
- `extracted/spritesheets/` - older spritesheet exports outside the current manifest
- `extracted/diagnostics/` - older targeted diagnostic PNGs
- `extracted/debug_layers/` - older layer dumps
- `extracted/misc/` - obsolete generated asset scratch space

Kept:
- `zax-assets/data/` and `zax-assets/polish/` - archive-verified source corpus
- `extracted/animations/`, `extracted/animations_8bit/`, `extracted/textures/`, `extracted/textures_8bit/`, and other current `tools/batch_extract.py` outputs
- `extracted/audits/visual_artifacts/` and `extracted/manifests/` - current validation outputs

## Cross-reference sanity
- README now points to FRM16_FORMAT.md, SEQ16_FORMAT.md, and IDA_PRO_ANALYSIS.md; links to removed docs were dropped.
- Manual diagnostic examples now avoid recreating the deleted stale output roots.

## Summary
- Deleted 9 outdated/contradictory docs and logs; updated 2 key docs and README.
- Removed obsolete generated asset roots; preserved engine-aligned outputs and current validation artifacts.
