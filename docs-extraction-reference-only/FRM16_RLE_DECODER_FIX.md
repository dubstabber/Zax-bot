# FRM16 RLE decoder fix — missing/transparent pixels root-cause and resolution

Date: 2025-10-15

## Summary
Some extracted FRM16 images (notably Dialog Window Frame, New Mission Complete Window, and Crystal Icon) rendered with missing or transparent pixels. Later IDA validation of `FRM_LoadFrame` corrected the earlier diagnosis: the header's per-layer size is the exact bitmap/stream byte count, there is no embedded 4-byte length inside the layer, and the scanline LUT follows immediately after that byte count. Tools should use `tools/frm16_decode.py` as the source of truth.

## Symptoms observed
- Alpha channel discrepancies vs. reference renders
  - Dialog Window Frame: 69,645 alpha pixels differed
  - New Mission Complete Window: 63,645 alpha pixels differed
  - Crystal Icon: 515 alpha pixels differed
- Visuals: gaps, missing edges, and incorrectly transparent regions.

## Format details (relevant to the bug)
For FRM16 type 0x44 (RLE):
- Each layer layout:
  - [0..size) compressed per-scanline stream
  - [size..size + height*4) LUT of uint32 scanline offsets
- The LUT offsets are relative to `layer_start`, i.e. the first byte of the compressed stream.
- The header's layer size equals `size`. When advancing through the file: `offset += size + height*4`.

This is now reflected in docs/FRM16_FORMAT.md (“Important decoder note”).

## The fix
- Use the single-source implementation in `tools/frm16_decode.py`, avoiding code drift.
- This decoder correctly:
  - Uses the header layer size as the bitmap/stream length
  - Interprets LUT offsets relative to `layer_start`
  - Handles alpha Layer 4 as either raw 1‑bit mask or RLE 16‑bit bitmap
  - Composites Layer 5 (semi‑transparent) using Layer 4

## Verification
Regenerated and compared against validation images (extracted/validation_frm16):
- Crater Icon — exact pixel match
- Dialog Window Frame — exact pixel match
- New Mission Complete Window — exact pixel match
- Crystal Icon — exact pixel match
- File Icon — exact pixel match

Additionally, alpha channel zero‑count matched reference for the failing cases (132,438; 146,040; 717 respectively).

## Impact and guidance
- Root cause fixed at the parser level; not a workaround.
- Centralizing FRM16 decoding (type 0x44) to tools/frm16_decode.py ensures future changes are in one place and stay consistent across tools.
- If future images show anomalies, first verify LUT base and layer-size handling before investigating blending.

## Files changed
- tools/frm16_decode.py - shared FRM16 decode path

## Next steps
- If we add new extraction tools, import/use tools/frm16_decode.py for FRM16 to avoid duplication.
