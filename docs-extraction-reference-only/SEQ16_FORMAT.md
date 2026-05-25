# SEQ16 Format Specification

## Overview

SEQ16 files in Zax: The Alien Hunter contain **animation sequences** - multiple FRM16 texture frames stored sequentially. These are used for animated sprites, UI elements, fonts, and visual effects.

## File Structure

```
[Header - 64 bytes]
[FRM16 Frame 0]
[FRM16 Frame 1]
...
[FRM16 Frame N-1]
```

## Header Format

| Offset | Size | Type    | Description                           |
|--------|------|---------|---------------------------------------|
| 0x00   | 4    | float32 | Unknown (often 27.0)                  |
| 0x04   | 4    | float32 | Unknown (often 207.0)                 |
| 0x08   | 4    | uint32  | Unknown (often 0)                     |
| 0x0C   | 4    | uint32  | **Frame count**                       |
| 0x10   | 4    | float32 | Likely FPS (often 30.0 or 1.0)        |
| 0x14   | 2    | uint16  | Unknown (often 0x1001 or 0x1000)      |
| 0x16   | var  | string  | Material name (e.g., "Transparent Mask") |
| ...    | ...  | ...     | Additional header data                |
| 0x46   | var  | bytes   | **First FRM16 frame starts here**     |

Note: The first FRM16 frame typically starts near offset 0x46, but this can vary with header string length. Do not hardcode 0x46; compute boundaries from FRM16 headers.

## Frame Storage

Frames are stored back-to-back; there is no explicit offset table:
- Each frame is an FRM16 block (with its own header)
- Frames are variable length (RLE compressed)
- Determine boundaries by reading each frame’s FRM16 header and computing its exact size (preferred). Marker scanning (0x1031) is only a fallback for quick triage.

## Frame Extraction Algorithm

```python
1. Read frame count from offset 0x0C
2. Seek to first frame start (typically ~0x46; compute from header/string length)
3. For i in [0..frame_count-1]:
   - Parse FRM16 header at current offset to compute exact frame size
   - Slice [cur, cur+size) as frame_i.frm16; advance cur += size
4. Optionally convert frames to PNG; for delta sequences use --composite for previews
```

### Robust frame boundary detection (preferred)
Instead of relying on the next 0x1031 marker, compute each frame’s size from its FRM16 header:
- If type == 0x40 (raw): size = 0x24 + width*height*2
- If type == 0x44 (RLE): size = 0x24 + sum(layer_size + height*4 for each non-zero layer_size in the 5 dwords at 0x10)
This matches the engine’s logic and guarantees exact splits even if 0x31 0x10 happens to occur inside compressed data.



## Playback and compositing behavior (important)

- Many sequences store frames as small delta overlays, not full images. Evidence:
  - Extremely small FRM16 payloads (e.g., 135–500 bytes) and per-frame width/height changing within the same sequence (e.g., Character Dialog.seq16).
  - These deltas are intended to be drawn over an existing surface (e.g., a UI window, a base sprite), without clearing between frames.
- FRM16 header fields:
  - 0x00: magic 0x1031
  - 0x02, 0x04: likely signed origin offsets (observed 0 for tested samples)
  - 0x06: width, 0x08: height (these can vary per frame)
  - 0x0C: type (0x40 raw, 0x44 layered RLE)
- Consequences for offline extraction:
  - Naively converting each frame to PNG can look like “interrupted” or partial sprites because you are seeing only the delta patch for that frame.
  - To reconstruct a more accurate animation preview, composite each frame onto an accumulator canvas:
    - Keep a persistent RGBA buffer; for each frame, decode to RGBA and copy only non-transparent pixels into the buffer.
    - Save the buffer as that frame’s PNG.
  - Note: Without the true render target and exact placement/clear semantics used in-game, this is an approximation. Some sequences rely on external base images (e.g., Dialog Window Frame) that are not embedded in the SEQ16 itself.

Extractor support:
- tools/extract_seq16.py now supports:
  - --to-png: Convert extracted FRM16 frames to PNGs
  - --composite: Accumulate delta overlays onto a persistent canvas (top-left anchored) for better previews of delta-encoded sequences

Timing hints:
- Strings in the binary mention “Obey Sequence FPS”, “Frames Per Second”, and “Use Velocity to Advance Frames”, suggesting:
  - Per-sequence FPS playback is supported (likely tied to the float at header 0x10).
  - Some animations may advance based on velocity/time rather than fixed FPS.
- Precise timing/control flow: see IDA notes at the end; further mapping in progress.


## Playback Timing and FPS Flow (engine-confirmed)

- FPS location in SEQ16: the float at header offset 0x10 is the sequence FPS.
- Storage in runtime object: copied into CSequence at offset +36 (float) during parsing.
- Rate query: Sequence_GetRate at 0x4A5980 (vtbl+0x68) returns the FPS from [sequence+36].
- Consumer of rate: animation tick at 0x517F20 calls vtbl+0x68, multiplies by dt, and advances progress; then wraps by frame_count.

Pseudocode of the update loop:
<augment_code_snippet mode="EXCERPT" path="docs/SEQ16_FORMAT.md">
````c
// 0x517F20 (simplified)
seq = ModelManager_GetElement(modelMgr, currentAnimIdx);
float rate = seq->vtbl[0x68/4](seq);  // 0x4A5980 → *(float*)(seq+36)
progress += rate * dt;                // advance animation time
progress = fmodf(progress, (float)frame_count);
````
</augment_code_snippet>

Notes:
- This ties the SEQ16 header’s FPS directly to playback speed via Sequence_GetRate.
- See docs/IDA_PRO_ANALYSIS.md for vtable-entry addresses and call-site details.

## Examples

### Single Frame Animation
```
File: Add Point.seq16
Size: 421 bytes
Frame count: 1
FRM16 markers: 1 at 0x46
```

### Multi-Frame Animation
```
File: Battery Level 1 Lights.seq16
Size: 4917 bytes
Frame count: 10
FRM16 markers: 10
Frame sizes: 486, 486, 486, 486, 486, 484, 484, 484, 486, 479 bytes
```

### Font/Character Set
```
File: Character Dialog.seq16
Size: 58926 bytes
Frame count: 255
FRM16 markers: 255
Purpose: Font glyphs (one per character)
```

## Common Animation Types

Based on file names and frame counts:

| Type | Example | Frames | Purpose |
|------|---------|--------|---------|
| **Static** | Add Point.seq16 | 1 | Single sprite |
| **Looping** | Battery Level 1 Lights.seq16 | 10 | Animated light effect |
| **Font** | Character Dialog.seq16 | 255 | Character glyphs (ASCII) |
| **Sprite Sheet** | Courier.seq16 | 94 | Character animations |
| **Large Animation** | Charms.seq16 | 29 | Complex animated object |

## Source SEQ Variant

The `.seq` files use the same surrounding sequence container as `.seq16`
(header, presence flags, optional absent frames, and metadata probing), but
present frames are embedded 8-bit FRM blocks with magic `31 08` instead of
FRM16 blocks with magic `31 10`. The batch extractor converts these source
animations through the 8-bit FRM decoder into `extracted/animations_8bit/`.

Corpus coverage after the source-SEQ pass:
- 212/212 `.seq` files parsed
- 7,605 present 8-bit frame PNGs emitted
- Placeholder sequences with absent frames still emit metadata JSON

## Relationship to Other Formats

- **FRM16**: Individual texture frames (see FRM16_FORMAT.md)
- **SEQ**: Source animation container with embedded 8-bit FRM frames
- **SEQ16**: Runtime format (16-bit color, RLE compressed)

## Extraction Tool

Use `tools/extract_seq16.py`:

```bash
# Extract single file
python3 tools/extract_seq16.py "zax-assets/data/Animation.seq16" output_dir

# Extract all SEQ16 files
python3 tools/extract_seq16.py "zax-assets/data" output_dir
```

Output structure:
```
output_dir/
  Animation_Name/
    frame_0000.frm16
    frame_0001.frm16
    ...
```

Use `--to-png` for standalone PNG conversion, or run `python3 tools/batch_extract.py`
to process the full known-format corpus into `extracted/`.

## Statistics

From zax-assets/data:
- **~300 SEQ16 files**
- Frame counts range from 1 to 255
- Total frames: thousands
- Largest file: Background.seq16 (543 KB, likely background animation)

## Notes

- All SEQ16 files share the same header structure
- Frame count at 0x0C is reliable and matches FRM16 splits
- Header[0x10] is a float that often encodes FPS when "Obey Sequence FPS" is used
- Header[0x00] and 0x04 are validation floats checked by the loader; not directly used in playback
- Material name at 0x16+ is usually "Transparent Mask" and influences default blit type
- First frame offset varies with header string length; do not hardcode 0x46

## Playback timing and update flow (confirmed)

Sequence helpers:
- GetFrameByProgress [0..1): 0x56BEC0 → computes index=floor(count*progress) (clamped) and calls vtbl+92
  - vtbl+92 target = GetFrameByIndex: 0x56BEA0
- FPS field (float) at CSequence+36; registered by 0x56AD20 ("Frames Per Second")

Runtime updater (consumer-driven):
- CEntityBehaviorIdle::tick (0x4990E0) advances animation each frame:
  - progress_next = progress + dt × rate × multiplier × |vel_scale|
    - rate is queried from the ACTIVE ANIM TRACK via its virtual method at vtbl+104 (0x68 offset) on the object returned by sub_48B590(entity)
    - multiplier is a behavior-owned scalar (UI/debug-tunable)
    - vel_scale flips by facing and uses an entity speed scalar
  - It wraps against the track’s limit at [track+72] (unsigned int) and emits updates via sub_560300 when crossing the boundary.
- Particle/velocity-driven variant (confirmed): CGravityParticleAI::tick at 0x529320
  - If "Use Velocity to Advance Frames" is enabled (flag at this+44), it computes:
    - speed = sqrt(vx^2 + vy^2) × scale, and
    - progress_next = progress + speed / track_rate, where track_rate = track->vtbl[+0x68](track)
  - This confirms the same vtbl+104 rate source is used by particles; when "Obey Sequence FPS" is enabled for the track, this rate corresponds to the sequence’s FPS.

Renderable hookup:
- CRenderablePolygon registers "Render/Cur Frame" at offset +148 (0x55D4A0). Its draw path (0x55DD90) selects which texture/frame to use for the current mode and copies bounds into its inner render unit; it expects the current frame selection to have been prepared by behaviors.

Loader/dispatch context:
- sub_56B780 → sub_56B890 → sub_416FE0 (validates header[0x00]≈27.0 and header[0x04] via sub_4153A0; then loads frames)

Related strings:
- "Frames Per Second" (0x6139E0), "Obey Sequence FPS" (0x61D804), "Use Velocity to Advance Frames" (0x61D890), "Render/Cur Frame" (0x6205F8)

Notes:
- The active animation track provides both: (a) rate via vtbl+104 and (b) wrap boundary at [track+72]. "Obey Sequence FPS" toggles whether the rate returned by vtbl+104 is the sequence’s FPS (CSequence+36) or another source; velocity-driven playback divides by this rate.


Obey Sequence FPS semantics (confirmed):
- Particle AI path sub_528A40 shows two behaviors:
  - If Obey Sequence FPS (flag at this+30) is ON:
    - delta_frames = track_rate × dt, where track_rate = track->vtbl[+0x68](track)
    - progress_next = fmod(progress + delta_frames, track_length) with track_length = *(unsigned int*)(track+72)
  - If Obey Sequence FPS is OFF:
    - delta_frames = (track_length × dt) / period, where period = sub_492080(entity)
    - progress_next = progress + delta_frames
- This confirms vtbl+0x68 returns the effective rate in frames-per-second when "Obey Sequence FPS" is enabled, and that the same track_length (at [track+72]) is the wrap boundary used across systems.
- The idle behavior (0x4990E0) uses the same track_rate but multiplies by play_ratio and velocity scale; particle velocity mode (0x529320) divides speed by track_rate.

## Active track retrieval and interface (confirmed)

- Getter: sub_48B590(entity)
  - Uses a global table at dword_6CFE08 (128-byte stride per slot) with lazy fill via a resource manager at dword_6CFDD8.
  - On first access it calls manager->vtbl[+4](manager, index) and then manager->vtbl[+148](..., &slot[120]) to resolve and cache the resource.
  - Returns the active track object via sub_5173F0(this[30]) once the slot is valid.
- Track interface used by all consumers:
  - rate: virtual at vtbl+0x68 (104) on the track object — treated as frames-per-second when "Obey Sequence FPS" is ON
  - length: unsigned int at [track+72] — the wrap boundary used by both idle and particle paths

Addresses for traceability:
- sub_48B590: active track getter (manager-backed)
- sub_5173F0: element-array getter (bounds-checked, returns track from instance[19])
- sub_4990E0: idle behavior tick; integrates dt × rate × play_ratio × |vel_scale|
- sub_529320: particle velocity-driven tick; uses speed / rate
- sub_528A40: obey-FPS helper; splits obey ON (FPS-based) vs OFF (period-based)
- sub_56AD20: CSequence property registration; FPS at offset +36

Active track class and vtable (current status):
- The object returned by sub_48B590(indexed via entity[30]) is the “active track” used by consumers.
- Interface (confirmed by callsites): rate via vtbl+0x68 (returns FPS when Obey Sequence FPS is ON), length at [track+72] (wrap boundary).
- Concrete type: unresolved name; propose placeholder: SequenceTrack (TBD). This object likely belongs to the model/sequence runtime set created by the model manager.
- Vtable address: TBD (see “How to resolve the concrete class/vtable in IDA” below). The rate method is the entry at +0x68.

How to resolve the concrete class and vtable in IDA (manual steps):
1) Go to dword_6CFDD8 (model manager singleton). In a live process this holds a vtable pointer; statically it may appear as uninitialized.
2) From sub_48B590, note manager virtuals used:
   - vtbl+0x04: manager->IndexToName(index)
   - vtbl+0x94: manager->LoadResolve(ValueName, name, out_slot+0x78)
   The latter writes a pointer to a container/object into [slot+0x78].
3) Follow what is written to [slot+0x78]; that is the container whose sub_5173F0(this[30]) returns an element from.
4) Inspect one returned element (double-click through) and open its vtable (first dword). The Rate() method is the entry at vtbl+0x68; length is a field at offset +0x48 (used as [track+72]).
5) Name the class (e.g., SequenceTrack) and annotate its vtable and Rate() method with xrefs to consumers: sub_4990E0, sub_529320, sub_528A40.

Animation update loop (state diagram):
<augment_code_snippet mode="EXCERPT">
````mermaid
flowchart LR
  E[Entity] -->|sub_48B590| T((Active Track))
  T -->|vtbl+0x68| R[rate (FPS when obey ON)]
  T -->|[+72]| L[length (wrap)]
  R --> I[Integrate progress]
  E --> I
  I -->|wrap vs L| W[Normalize/Wrap]
  W --> Draw[Renderable uses current frame]
````
</augment_code_snippet>

Notes on Rate() semantics (confirmed by consumers):
- Idle: progress += dt × Rate() × play_ratio × |vel_scale|; wrap against [track+72].
- Particles (velocity mode): progress += speed / Rate().
- Obey Sequence FPS ON: Rate() equals the sequence’s FPS (CSequence+36); OFF: alternative timing path uses period from sub_492080.


## Verification

Extraction tested on:
- ✅ Single frame files (Add Point.seq16)
- ✅ Multi-frame animations (Battery Level 1 Lights.seq16 - 10 frames)
- ✅ Large animations (Character Dialog.seq16 - 255 frames)
- ✅ All extracted frames convert successfully to PNG

## References

- FRM16 texture format: docs/FRM16_FORMAT.md
- Extraction tool: tools/extract_seq16.py
- FRM16 converter: tools/frm16_decode.py
- Corpus extractor: tools/batch_extract.py


## IDA Pro cross-references and loader flow (confirmed)

- Path construction selector: Sequence_BuildPath (0x56B780)
  - Chooses extension based on desired bit-depth: .seq, .seq16, .seq24, .seq32
  - Uses sub_4E02C0 for .seq/.seq16 and sub_4DFFD0 for .seq24/.seq32
- Higher-level open wrapper: Sequence_LoadFromFile (0x56B890)
  - Calls Sequence_BuildPath to build the path, opens the file (sub_5242B0), and dispatches to the sequence loader
- Sequence loader/validator: sub_416FE0 (generic dispatcher)
  - Reads two float values (via sub_4A2270); first is often 27.0 (matches offset 0x00)
  - Compares second float with a derived value (sub_4153A0), then calls the concrete parse routine (vtbl+60 on the sequence object)
- Post-load preparation: Sequence_PrepareAndValidate (0x5600F0)
  - Computes frames-per-rotation and validates divisibility; fatal on mismatch
  - Allocates/maintains per-frame 8-byte records at [this+68] (signed x/y offsets per frame), then calls Sequence_ComputeBounds (0x56B570)
- FRM frame reader: FRM_LoadFrame (0x4A9340)
  - Validates FRM16 header and copies/allocates up to 5 layers (raw 0x40, layered RLE 0x44)

Notes:
- A tiny sample (Add Point.seq16) shows first FRM16 magic (0x1031) at offset ~0x46, aligning with this spec (actual first-frame offset varies with header string length).
- Frames are stored back-to-back with no explicit offset table; computing exact size from each frames FRM16 header is robust and matches the headers frame count at 0x0C.



## Header layout and per-frame records (confirmed from engine)

Header read sequence (as parsed by sub_416FE0 → Sequence_ParseSEQ16 → Sequence_ReadFramesSEQ16):
- 0x00: float keyA (commonly 27.0; special-case 24.0 → treated as 25.0 for legacy content)
- 0x04: float keyB (must equal sub_4153A0(vtbl+8 on the sequence object) to proceed)
- 0x0C..0x1B: 16-byte block copied into the sequence object starting at [this+0x0C]. This block includes:
  - frameCount (copied/available at [this+0x28])
  - FPS (float)
  - two additional 32-bit fields (TBD/flags)

After the 16-byte block:
- For i in [0 .. frameCount-1]:
  - Read 1 byte presence flag; if 0 → store null pointer for that frame; if nonzero → construct frame object and call FRM_LoadFrame(frame, stream).
- After all frames are read:
  - Read per-frame anchor records: size = 8 × frameCount bytes into [this+0x44].

Per-frame 8-byte record structure (as applied by Sequence_PrepareAndValidate):
- int16 x at +0; int16 pad0 at +2
- int16 y at +4; int16 pad1 at +6
Anchors are applied as negative offsets: sub_4ABAC0(-x, -y).

Notes:
- The engine does not store a separate frame-offset table; frames are streamed with 1-byte flags immediately preceding each FRM16 header.
- The first embedded FRM16 typically begins immediately after the header block and the first presence flag; exact byte position varies if prior metadata is present, but scanning for 0x31 0x10 (FRM16 magic) remains robust.


### Variable metadata segment between header and frames

- After the 16-byte block, the engine’s concrete parser tolerates a variable metadata segment (e.g., name string, bounds). We locate the true start of the flag+frame stream by probing for a position where `frame_count` iterations of: read 1-byte flag; if 1 then FRM16 header follows and sizes cleanly, succeeds.
- This matches observed files (e.g., "!None.seq16", where the first present flag appears at 0x45 and is immediately followed by the 0x1031 FRM16 magic at 0x46).

Example parser excerpt (engine-aligned probing):
<augment_code_snippet path="tools/extract_seq16.py" mode="EXCERPT">
````python
for p in range(0x1C, 0x1C + 0x2000):
    if data[p] in (0, 1):
        ok, frames, end_pos = try_parse_from(p)
        if ok and sum(f['present'] for f in frames) >= 1:
            anchors_ok = end_pos + frame_count * 8 <= len(data)
            if anchors_ok: break
````
</augment_code_snippet>


## Properties.txt format (rotation grouping)

The engine ensures a sequence has a Properties.txt at:
- "%s/-%s/Properties.txt" (rootDir / "-" + seqName / Properties.txt), per Sequence_EnsureProperties at 0x56B320.

Relevant keys (observed strings in binary):
- "Rotations"
- "Num Rotations" (synonym)

Frames-per-rotation is derived as: frames_per_rotation = total_frames / rotation_count.
Sequence_PrepareAndValidate (0x5600F0) fatal-errors if totalFrames % rotationCount != 0.

Example Properties.txt:
<augment_code_snippet mode="EXCERPT">
````ini
# Sequence properties for rotation grouping
Rotations=8
````
</augment_code_snippet>

Extractor behavior:
- Looks for "-<seqName>/Properties.txt" (preferred) and also "Properties.txt" in the same directory as a fallback.
- Parses rotation_count from either "Rotations" or "Num Rotations".
- Computes frames_per_rotation from the SEQ16 header's total frame count.
- Organizes extracted frames into rotation_<NN>/ subdirectories and emits rotation_count and frames_per_rotation in metadata.


### Rotation-aware extraction usage (extract_seq16.py)

- Place a Properties.txt with rotations near the sequence:
  - Preferred: <seq_dir>/-<SeqName>/Properties.txt
  - Fallback: <seq_dir>/Properties.txt
- Supported keys (case-insensitive): Rotations, Num Rotations

Example:
<augment_code_snippet mode="EXCERPT">
````ini
# zax-assets/data/-Character Dialog/Properties.txt
Rotations=5
````
</augment_code_snippet>

Run extractor:
<augment_code_snippet mode="EXCERPT">
````bash
python3 tools/extract_seq16.py "zax-assets/data/Character Dialog.seq16" extracted_seq16
````
</augment_code_snippet>

Resulting structure (for 255 frames, 5 rotations):
<augment_code_snippet mode="EXCERPT">
````text
extracted_seq16/Character Dialog/
  rotation_00/  # 51 frames
  rotation_01/  # 51 frames
  rotation_02/  # 51 frames
  rotation_03/  # 51 frames
  rotation_04/  # 51 frames
  Character Dialog_metadata.json
````
</augment_code_snippet>

Metadata highlights:
<augment_code_snippet mode="EXCERPT" path="extracted_seq16/Character Dialog/Character Dialog_metadata.json">
````json
{
  "rotation_count": 5,
  "frames_per_rotation": 51,
  "frames": [
    { "index": 0,  "rotation": 0, ... },
    { "index": 51, "rotation": 1, ... },
    { "index": 102,"rotation": 2, ... }
  ]
}
````
</augment_code_snippet>

Behavior when invalid:
- If frame_count is not divisible by Rotations, the extractor emits a warning and falls back to single-rotation output (no rotation_* subdirs), matching engine constraints from Sequence_PrepareAndValidate.
