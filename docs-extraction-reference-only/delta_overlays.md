## Zax frame/sequence “delta overlays” — findings and extraction plan

Summary
- Zax stores 2D art in Reflexive FRM/FRM16 formats. FRM16 is a cached, 16‑bit, possibly multi‑layer variant used broadly for UI, sprites, etc. Sequences (.seq/.seq16) organize multiple frames per animation/rotation.
- IDA shows FRM loader at FRM_LoadFrame (0x4A9340). It reads a header (anchorX/Y, width/height, type) and then up to four data sections. Each section consists of a single data blob plus a per‑frame table of 4‑byte offsets (count = frameCount). This implies per‑frame sub‑chunks inside each section.
- A debug routine Dump_FrameDeltas (0x55F9B0) logs per‑frame draw offsets: this[10] = frameCount; this[17] -> array of (dx,dy). These appear to be the per‑frame registration deltas applied when rendering frames.
- FRM16 layering (empirical from assets and prior notes):
  - Layer 1: opaque base (RLE or raw)
  - Layer 4: alpha mask for the semi‑transparent overlay (no RLE if mask‑only size matches)
  - Layer 5: semi‑transparent RGB overlay (RLE)
  - Layers 2–3 unused in Zax (observed)

What are “delta overlays” here?
- In practice we see two delta concepts:
  1) Per‑frame draw deltas (dx,dy) — how to shift each frame relative to an anchor (logged by Dump_FrameDeltas).
  2) Per‑frame pixel overlays — the FRM16 layer 5 RGB combined with layer 4 alpha; this is an additive/replace overlay region that changes between frames and is applied on top of the base.
- The user’s goal (“use delta overlays as separate PNG images so each frame … is saved as separate image”) maps naturally to exporting, for each frame, the overlay‑only RGBA image (layer5 RGB, layer4 alpha), without compositing onto the base.

Reverse‑engineered anchors and deltas
- FRM_LoadFrame stores anchor and dimensions in WORD fields; a packed origin (v51) is split and negated into this+4/this+5 (int16). Width/height go to this+9/this+10.
- A separate component maintains per‑frame deltas and logs them via Dump_FrameDeltas. We haven’t fully typed that class yet, but the (dx,dy) array is eight bytes per frame and lives at this+17.

Extraction approach
- For .frm16 frames (including frames emitted from .seq16 into a scratch frame directory):
  - Decode RLE layer 5 (RGB) and layer 4 (alpha) if present; build overlay‑only RGBA by taking RGB from layer 5 and alpha from layer 4 (or 50% heuristic if mask is missing).
  - Save each frame’s overlay PNG to extracted/overlays/<sequence>/frame_XXXX_overlay.png.
- If only raw (type 0x40) is present or layer 5 is absent, either write nothing or an empty image.

Status
- FRM loader and per‑frame LUTs identified at 0x4A9340.
- Per‑frame delta logger identified at 0x55F9B0.
- Working FRM16 decode logic lives in `tools/frm16_decode.py`; extraction tools should import that module instead of keeping separate RLE copies.
- Persistent `zax-assets/seq16_frames/` exports were removed as stale. If this older overlay workflow is revisited, generate intermediate frames into `/tmp` or an `extracted/audits/...` scratch directory instead of restoring that root.

Next steps
- Update `tools/extract_delta_overlays.py` to walk an explicit scratch frame root and write overlay PNGs per frame.
- Optionally extend to emit base‑only PNGs and a JSON sidecar carrying anchor and (dx,dy) deltas per frame for downstream animation.



## RLE scanline/LUT layout (validated)
- For FRM16 type 0x44 sections, each non-zero layer chunk is laid out as:
  - [0..size): compressed scanline stream
  - [size..size + height*4): scanline LUT of uint32 offsets (one per row)
- The header size table at 0x10 stores the bitmap/stream byte count exactly; there is no embedded 4-byte comp_len inside the layer data.
- The LUT entries are offsets relative to `layer_start`, i.e. the first byte of the bitmap/stream.
- File packing advances between sections by `size + height*4`.

Validation: `tools/frm16_decode.py` and `tools/extract_seq16.py` now use this no-embedded-length layout. The same `size + height*4` rule is also needed for raw type 0x40 frames embedded in SEQ16 files, because those raw frames can include a trailing scanline LUT.
