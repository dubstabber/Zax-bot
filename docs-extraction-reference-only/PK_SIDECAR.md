# PK Voiceover Sidecar Classification

`zax-assets/data/Azah Pan speech at end.pk` is the only shipped `.pk` file in
the unpacked corpus. It is paired by stem with `Azah Pan speech at end.ogg`,
but the executable paths checked so far do not prove a `.pk` decoder or runtime
consumer.

## IDA evidence

- `0x5A4CA0` prepares dialog voiceovers and builds a `%s.wav` candidate from
  the dialog line name.
- `0x5A4F50` and `0x5A5130` pass resolved dialog/voice-log sound paths to the
  stream player.
- `0x57E690` accepts `.wav` and `.ogg` fallback sound assets; no `.pk` branch
  was observed.
- `0x595BE0 -> 0x587310` streams the resolved sound path through DirectSound.
- String search for `.pk` or the shipped sidecar name did not find a direct
  loader reference in the current IDA database.

## Extraction policy

The batch pipeline preserves the raw sidecar and writes conservative metadata
under `extracted/dialogue_sidecars/`. It records size, SHA-256, sibling audio
candidates, and the IDA evidence above.

Do not name internal fields or decode payload structures until a real
executable consumer is identified. Byte-pattern guesses are intentionally out
of scope.
