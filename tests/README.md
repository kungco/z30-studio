# Regression coverage

Run `python -m unittest discover -v`. These tests do not call Codex or any paid API.

`test_imaging.py` covers sensor-relative RAW inset crops with active margins, odd half-size boundaries, all eight LibRaw flip patterns, all eight EXIF orientations, ICC sRGB, an independently generated wide-gamut matrix/TRC profile, grayscale, palette and RGBA inputs, transparent-to-white composition in linear light, PNG gAMA, and explicit rejection of ambiguous CMYK / broken profiles.

The generated fixtures are synthetic; they are not measurements of a printer, display or camera. Valid printer-specific CMYK ICC rendering has not been verified with real print samples. Non-square RAW pixels and 16-bit RGB PNG input are explicitly unsupported rather than silently misinterpreted.

Optional real RAW integration tests require original CC0 samples from raw.pixls.us (file IDs 5812 and 5813), named `Z30-12bit.NEF` and `Z30-14bit.NEF` in a directory referenced by `Z30_RAW_SAMPLES`. The files are not committed. Without this environment variable, unittest reports the real RAW check as skipped, not passed.

```powershell
$env:Z30_RAW_SAMPLES = 'D:\your-test-data'
python -m unittest discover -v
```

The integration check verifies both bit depths, original-file hashes, portrait dimensions and linear preview/full alignment. Mirrored/odd-boundary directions are synthetic tests; only orientation flip=5 has been verified against the two real Z30 samples.

Benchmarks use `tools/benchmark_pipeline.py`. Each repeat runs in a fresh child process; the OS peak RSS is a process high-water mark including earlier stages in that child, not a per-function allocation measurement. Timing is wall-clock median. Compare runs on the same machine and do not infer universal speedups from these samples.
