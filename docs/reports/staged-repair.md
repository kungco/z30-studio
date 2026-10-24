# Staged image pipeline repair

Baseline: commit `4a5c38e`. Changes are implemented in source, with a separate commit per priority stage. All fixtures are generated or the previously documented CC0 Z30 samples; no private photos or Codex credentials are part of this report.

## P0 — geometry and input colour

| Requirement | Changed files | Verification |
| --- | --- | --- |
| Sensor crop / orientation / preview alignment | `imaging.py`, `server.py`, `tests/test_imaging.py` | Sensor-relative inset translated against active margins; all eight LibRaw transforms; fractional odd half-size edges; invalid crop and unexpected shape handling; user rotation/crop combinations |
| JPEG/PNG colour management | `imaging.py`, `server.py`, `web/app.js` | ICC sRGB and synthetic wide RGB, palette ICC, gray/alpha, 16-bit grayscale precision, linear-light alpha composition, PNG gAMA, corrupt or mismatched ICC and untagged CMYK rejection; import warnings visible in the app |
| Different orientations / colour / RAW depth | `tests/test_imaging.py`, `tests/color_profiles.py`, `tests/README.md` | 28 tests including the original 11 Codex tests; actual 12/14-bit Z30 NEF hashes and 3712×5568 portrait dimensions preserved; 1067×1600 previews aligned to full development |
| Reproducible comparison | `tools/benchmark_pipeline.py`, report JSON files | Two isolated-process repeats per sample; wall-clock medians and OS process peak RSS, not Python-only allocator counts |

The old implementation was independently executed before replacement. A crop with active margin (2,2) and sensor inset (4,4,8,6) incorrectly returned (4,4,8,6), instead of active-relative (2,2,8,6). A 50% transparent black pixel was decoded as linear (0,0,0), rather than approximately (0.498,0.498,0.498) over white. Untagged CMYK was silently interpreted via a generic RGB conversion. These observations are recorded in `reproduced-bugs.json`.

P0 residual risks / unverified cases:

- Only LibRaw flip=5 is present in the two actual camera samples. Other orientations, active margins and odd inset boundaries are synthetic regression checks, not additional camera captures.
- The wide-gamut ICC fixture is a generated matrix/TRC profile, not a display calibration. Real printer-specific CMYK rendering has not been verified against print targets. Missing CMYK profiles are rejected instead of guessed.
- Non-square-pixel RAW and 16-bit RGB/RGBA PNG input are explicitly rejected. Gray 16-bit PNG is supported. This prevents Pillow's implicit reduction of RGB PNG to 8 bits from masquerading as a high-depth decode.
- Full-resolution and half-size demosaicing may differ in fine detail. The real-RAW check uses an explicit linear-light mean absolute error threshold, not a claim of bit-identical demosaicing.
- Untagged RGB is necessarily ambiguous: the app warns and assumes sRGB. Valid PNG gAMA/cHRM or explicit EXIF Adobe RGB is honored. ICC profiles take priority over those hints.

All source images remain read-only inputs. Local temporary copies and generated comparison images are kept outside the Git repository.

### P0 benchmark (actual 14-bit sample, same host)

| Stage | Baseline seconds / peak MiB | P0 seconds / peak MiB |
| --- | --- | --- |
| decode_full | 1.1181 / 632.2 | 1.0475 / 399.1 |
| decode_preview | 0.5083 / 632.2 | 0.4686 / 399.1 |
| decode_preview_cached | 0.0000 / 632.2 | 0.0000 / 399.1 |
| develop_full | 4.7400 / 632.2 | 5.1048 / 612.5 |
| encode_jpeg | 0.1055 / 632.2 | 0.1035 / 612.5 |
| preview_pipeline_cached | 0.6670 / 632.2 | 0.7118 / 612.5 |

12bit full-output comparison thumbnail (same stress settings): mean absolute 8-bit difference 0.000000, maximum 0. Output comparison is not a colour-target accuracy measurement.

14bit full-output comparison thumbnail (same stress settings): mean absolute 8-bit difference 0.000000, maximum 0. Output comparison is not a colour-target accuracy measurement.
