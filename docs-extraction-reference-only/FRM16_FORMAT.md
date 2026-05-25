# FRM16 File Format Specification

## Overview

FRM16 files are texture/image files used in Zax: The Alien Hunter and related Reflexive Entertainment games (Lionheart, Ricochet). They store 16-bit color images with optional transparency using Run-Length Encoding (RLE) compression.

## File Structure

```
[Header: 36 bytes]
[Layer 1 Data: Opaque/Base Layer]
[Layer 2 Data: Unused in Zax]
[Layer 3 Data: Unused in Zax]
[Layer 4 Data: Alpha/Transparency Mask]
[Layer 5 Data: Semi-transparent RGB]
```

## Header Format (36 bytes / 0x24)

| Offset | Size | Type   | Description                                    |
|--------|------|--------|------------------------------------------------|
| 0x00   | 2    | uint16 | Magic/Signature: 0x1031 (little-endian)       |
| 0x02   | 2    | int16  | Anchor X (registration point)                  |
| 0x04   | 2    | int16  | Anchor Y (registration point)                  |
| 0x06   | 2    | uint16 | Width in pixels                                |
| 0x08   | 2    | uint16 | Height in pixels                               |
| 0x0A   | 2    | uint16 | Flags (0 = normal, 107 = immediate)           |
| 0x0C   | 2    | uint16 | Type: 64 (0x40) = raw, 68 (0x44) = RLE       |
| 0x0E   | 2    | uint16 | Unknown/Padding (usually 0)                    |
| 0x10   | 4    | uint32 | Layer 1 bitmap size (opaque layer)            |
| 0x14   | 4    | uint32 | Layer 2 bitmap size (unused)                  |
| 0x18   | 4    | uint32 | Layer 3 bitmap size (unused)                  |
| 0x1C   | 4    | uint32 | Layer 4 bitmap size (alpha mask)              |
| 0x20   | 4    | uint32 | Layer 5 bitmap size (semi-transparent RGB)    |

## Layer Structure

Each layer consists of:
1. **Bitmap Data** - Pixel data (compressed or uncompressed)
2. **Scanline Lookup Table (LUT)** - Array of uint32 offsets to each row

### Type 64 (0x40) - Uncompressed

- No compression applied
- Bitmap size = width × height × 2 bytes
- Each pixel is a 16-bit RGB565 value
- Runtime files can still carry the layer size table at 0x10 and a scanline LUT after the raw bitmap. When the size table is present, frame size is `0x24 + sum(layer_size + height*4 for each non-zero layer)`.
- Some raw frames in SEQ16 files rely on this trailing LUT; extractors must include it when splitting embedded frames.

### Type 68 (0x44) - RLE Compressed

- Uses Run-Length Encoding
- Zax `.frm16` files commonly store the full type word as `0x2044`; the low byte
  `0x44` is the generic RLE storage mode used by `FRM_LoadFrame`.
- Bitmap data is exactly the number of bytes indicated by the per-layer size in the header (no embedded 4-byte comp_len)
- The scanline LUT (height × 4 bytes) immediately follows the bitmap data
- LUT offsets are 32-bit little-endian values relative to the start of the bitmap data
- Supports transparency via skip commands


### Important decoder note
- For Type 64 and Type 68 layers with non-zero layer sizes, each layer block uses this layout:
  - [0 .. size) bitmap data (raw pixels for Type 64, compressed scanlines for Type 68)
  - [size .. size + height*4) scanline LUT of uint32 offsets
- The scanline LUT offsets are relative to layer_start (the beginning of the bitmap data).
- File packing advances by: offset += size + height*4.

## Pixel Format

## Alpha mask details (Layer 4)
- When Layer 4 size equals ceil(width*height/8), it is a raw 1-bit mask (LSB-first) but still has a scanline LUT immediately following the data; advance by sz + height*4.
- Bit packing is LSB-first within each byte. When unpacking in NumPy, use np.unpackbits(mask_bytes, bitorder='little').
- Most non-bitmask Layer 4 sections are byte-alpha rows with the same height-entry LUT: each row span starts at x=0, may end before width, and stores 8-bit alpha values directly.
- Rare fallback Layer 4 sections can be RLE-compressed 16-bit bitmaps; decode like other RLE layers and threshold alpha as (pixel != 0).


Pixels use **RGB565** encoding (16-bit):
- Bits 15-11: Red (5 bits)
- Bits 10-5: Green (6 bits)
- Bits 4-0: Blue (5 bits)

To convert to 8-bit RGB:
```
R8 = (R5 * 255) / 31
G8 = (G6 * 255) / 63
B8 = (B5 * 255) / 31
```

## RLE Compression Scheme (Type 68)

Each scanline is compressed independently using a command-based RLE:

### Command Byte Format

The first byte of each run determines the operation:

| Bit 7 | Bit 6 | Bits 5-0 | Operation                                    |
|-------|-------|----------|----------------------------------------------|
| 1     | x     | count    | **Skip**: Skip `count & 0x7F` pixels (transparent) |
| 0     | 1     | count    | **Copy**: Copy `count & 0x3F` literal pixels (or 64 if count=0) |
| 0     | 0     | count    | **Repeat**: Repeat next pixel `count & 0x3F` times (or 64 if count=0) |

### RLE Examples

**Example 1:**
```
81 42 ff fe ff fc 05 00 00
```
Decodes to:
```
[skip 1] [copy 2: ff fe, ff fc] [repeat 5: 00 00]
Result: __ __ ff fe ff fc 00 00 00 00 00 00 00 00 00 00
```

**Example 2:**
```
C3 12 34 56 78 9A BC
```
Decodes to:
```
[copy 3: 12 34, 56 78, 9A BC]
Result: 12 34 56 78 9A BC
```

## Layer Usage in Zax

### Layer 1 (Opaque/Base)
- Contains the main opaque image data
- Pixels with value 0x0000 are treated as fully transparent
- Always present if image has content

### Layers 2 & 3
- Runtime FRM16 files observed so far leave these at size 0.
- 8-bit source FRM/SEQ variants do use the five generic frame layers as alpha
  buckets. The current extractor exports 8-bit layers as:
  - Layer 1: 100% alpha
  - Layer 2: 80% alpha
  - Layer 3: 60% alpha
  - Layer 4: 40% alpha
  - Layer 5: 20% alpha
- This is backed by `FRM_LoadFrame` loading all five sections generically and
  CSequence registering the `Load/Use 8 Bit Alpha` flag.

### Layer 4 (Alpha Mask)
- Contains transparency information for Layer 5
- Can be either:
  - **Raw bit mask**: 1 bit per pixel, size = (width × height + 7) / 8
  - **Byte alpha rows**: one 8-bit alpha byte per stored pixel row span, using the layer LUT
  - **RLE-compressed bitmap**: 16-bit values where non-zero = visible
- Only present when Layer 5 exists

### Layer 5 (Semi-transparent RGB)
- Contains RGB565 color data for semi-transparent pixels
- Uses the normal 16-bit RLE command stream, not 8-bit palette indices
- Uses alpha values from Layer 4
- Allows for smooth transparency effects

## Scanline Lookup Table (LUT)

- Located immediately after bitmap data
- Size: height × 4 bytes
- Each entry is a uint32 offset into the bitmap data
- Points to the start of each scanline

For Type 64:
```
LUT[0] = 0
LUT[1] = width * 2
LUT[2] = width * 4
...
```

For Type 68:
```
LUT[0] = 0  (start of bitmap data)
LUT[1] = offset to row 1
LUT[2] = offset to row 2
...
```

### RLE command bytes (from Zax.exe)
- If (cmd & 0x80): skip transparent by count = (cmd & 0x7F), with 0 meaning 128
- Else if (cmd & 0x40): literal run, count = (cmd & 0x3F), with 0 meaning 64, followed by count 16-bit pixels
- Else: repeat run, count = (cmd & 0x3F), with 0 meaning 64; read one 16-bit pixel and repeat count times


## Transparency Handling

1. **Type 64 (Raw)**: Pixel value 0x0000 = transparent, others = opaque
2. **Type 68 (RLE)**:
   - Skip commands create transparent pixels
   - Layer 4 provides alpha values or alpha mask for Layer 5
   - Layer 1 pixels written by literal/repeat commands are visible, even when
     the RGB565 value is `0x0000`. `Dialog Window Background.frm16` depends on
     this: it writes a full black panel through RLE commands, and treating
     written black as transparent produces a blank artifact.


## Layer Compositing / Blending Rules

Zax uses table-driven blitters initialized in sub_433000, and sub_527420 builds
BlackBias mappings used by some render modes. For extraction, do not replace
Layer 4 with a brightness heuristic when Layer 4 byte alpha exists: the frame
data already contains the alpha values used by anti-aliased fonts, shadows, and
small UI/particle sequences.

Current PNG extraction uses:

```
base_rgb565   = L1 RGB565 data
base_alpha    = 255 where Layer 1 was written by RLE literal/repeat commands
overlay_rgb   = L5 decoded as RGB565 RLE
overlay_alpha = L4 byte alpha, bitmask alpha, or fallback threshold alpha

if base_alpha == 0:
    out_rgb = overlay_rgb
else:
    out_rgb = alpha_blend(overlay_rgb, base_rgb, overlay_alpha)
out_alpha = max(base_alpha, overlay_alpha)
```

This preserves black shadows and low-alpha font pixels that become invisible if
Layer 5 is treated as palette indices or if Layer 4 is decoded as 16-bit RLE
when it is actually byte alpha.

### Decoder pitfalls (what can go wrong)
- Assuming an embedded 4-byte comp_len at the start of the bitmap data will misalign parsing; there is no such header.
- LUT offsets are relative to the start of the bitmap data (layer_start), not layer_start + 4.
- Layer 4 (raw 1-bit alpha) also includes a LUT; always advance by sz + height*4 when present.
- Layer 4 is usually byte alpha rows when every LUT row span fits within width; treating those bytes as 16-bit RLE drops font/shadow alpha.
- Layer 5 is RGB565 RLE; treating it as 8-bit palette indices corrupts colors and can make black overlays disappear.
- For RLE layer 1, using `pixel != 0` as the only alpha source drops written
  black pixels. Preserve the RLE written mask separately from the RGB565 value.

## Implementation Notes

- The format is designed for fast loading without decompression overhead
- FRM16 files are the 16-bit runtime frame variant used by the game
- Zax also ships 8-bit `.frm` source/runtime frame variants in `Data.dat`; decode those with `tools/frm_decode.py`
- The original `Data.dat` and `Polish.red` archives are recoverable through local ZIP records with `tools/extract_archives.py`

## Related Formats

- **FRM**: 8-bit indexed frame variant present in Zax and decoded with `GamePalette.pal`
- **SEQ16**: Animation sequences containing embedded FRM16 frames
- **SEQ**: Animation sequences containing embedded 8-bit FRM frames
- **MDL/MDL16**: IDA-confirmed POLY2D collision/path metadata, not renderable 3D models

## References

- Based on analysis of Zax.exe (MD5: bbc2d58b35d60b6cbc67d178e84a3301)
- Documentation from Lionheart modding community
- Reverse engineering of Reflexive Entertainment game formats



## Reverse-engineering notes (from Zax.exe)
- Loader function (FRM_LoadFrame at 0x4A9729) reads a 0x24-byte header; first byte must be 0x31 ('1').
- Width/Height are 16-bit little-endian at 0x06/0x08.
- Per-layer sizes (5 x uint32 at 0x10) are read verbatim; for each non-zero size, the loader reads exactly `size` bytes of bitmap data, then reads a scanline LUT of `height*4` bytes.
- LUT offsets are 32-bit little-endian, relative to the start of that layer's bitmap data.
- No embedded 4-byte comp_len exists inside the bitmap data.
- Raw 1-bit alpha (when present) still has a LUT; bits are packed LSB-first.
