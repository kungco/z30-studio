# Staged image pipeline repair

Baseline: commit `4a5c38e`. Changes are implemented in source, with a separate commit per priority stage. All fixtures are generated or the previously documented CC0 Z30 samples; no private photos or Codex credentials are part of this report.

## P0 — geometry and input colour (`a14ceab`)

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

## P1 — tone, high-depth export, task lifecycle and performance

| Requirement | Changed files | Verification |
| --- | --- | --- |
| Smooth tone / shoulder / shadows | `processing.py`, `server.py`, `tests/test_processing.py` | Black-anchored monotonic luminance mapping, C1 rational shoulder and a bounded quadratic-sampled tone table; interpolation error below 2e-6 linear units in the tested range |
| Clipping, warm hues and saturation | `processing.py`, `tests/test_processing.py` | OKLab hue-axis saturation, chroma reduction for out-of-gamut pixels, soft moderation of positive boosts in a warm hue band; synthetic warm patches, neutrals and saturated inputs |
| True RGB16 TIFF | `server.py`, `processing.py`, `requirements.txt`, `start.ps1`, `web/index.html`, `web/app.js` | UInt16 RGB TIFF with 16 bits/sample, 3 channels, sRGB ICC and more than 256 levels; sharpening remains float32; actual 14-bit NEF TIFF round-trip and HTTP response verified |
| Async/photo lifetime | `codex_assist.py`, `server.py`, `web/app.js`, `tests/test_lifecycle.py`, `tests/test_http.py` | Close kills a running child, late results cannot publish, completed stale results are invalidated, server returns HTTP 410, shutdown rejects new jobs; existing CLI auth and command flags retained |
| Per-stage performance | `tools/benchmark_pipeline.py`, isolated report JSON files | Fresh process for each decode / preview / development / JPEG encode / TIFF export stage; no input array is needlessly retained while measuring a separate TIFF export |

An initial exponential shoulder failed the neutral/highlight regression because it rounded bright values prematurely to the export maximum. The actual curve was replaced by a rational tail; the original strict test was retained. A coarse initial tone lookup failed the 2e-6 error bound; its sampling density was increased instead of loosening the test. Colour fixtures now use independent W3C reference matrices rather than sharing the production xy-to-matrix implementation.

The final P1 suite has 44 tests, including all 11 original tests. CI is added for generated-fixture regressions on Windows and Linux. Real RAW samples are optional and explicitly skipped when not provided; they were supplied for local validation.

P1 residual risks / unverified cases:

- Tone and colour appearance deliberately differ from the old clipping-based pipeline. This is not colour-target calibration or equivalence to Nikon rendering.
- The warm hue band is a heuristic, not a face/skin detector. Only generated warm patches have a numeric hue-preservation test; no real skin colour target or portrait camera sample was supplied.
- The high-depth path retains LibRaw's 16-bit output and float32 processing, but cannot recover data already clipped by the sensor or by LibRaw's integer RGB output.
- The 32,769-node tone table has a measured numeric approximation bound; it is not an assertion of bit-identical output to analytic evaluation at all possible float inputs.
- Closing a photo terminates its local Codex process and rejects late output. This does not promise cancellation/refund of a request already received by a remote service.
- Cancellation and publication races are deterministic fake-process tests. No additional paid API dependency or quota-consuming live Codex test was introduced; CLI authentication and existing tests are preserved.
- Printer-specific CMYK, non-square RAW, 16-bit RGB PNG and actual camera orientations other than flip=5 retain the P0 verification limits.

References: [LibRaw sizes](https://www.libraw.org/docs/API-datastruct.html), [Pillow CMS](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html), [PNG metadata](https://www.w3.org/TR/png-3/), [W3C colour conversion reference](https://www.w3.org/TR/css-color-4/#color-conversion-code), [OKLab](https://bottosson.github.io/posts/oklab/), [tifffile](https://www.cgohlke.com/docs/tifffile/).

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

### P1 paired isolated benchmark (actual 14-bit sample)

| Stage | Baseline seconds / peak MiB | P1 seconds / peak MiB |
| --- | --- | --- |
| decode_full | 1.1658 / 632.5 | 1.0335 / 400.2 |
| decode_preview | 0.5201 / 187.9 | 0.4728 / 155.0 |
| decode_preview_cached | 0.0000 / 187.9 | 0.0000 / 154.8 |
| develop_full | 4.7201 / 632.6 | 4.4071 / 430.0 |
| encode_jpeg | 0.1225 / 632.5 | 0.1208 / 429.9 |
| preview_pipeline_cached | 0.6949 / 187.9 | 0.5441 / 154.5 |
| preview_pipeline_repeat | 0.6713 / 187.8 | 0.4883 / 154.8 |
| export_tiff16 | not implemented | 8.3051 / 632.3 |

Under the same stress settings, 14-bit sample red-channel export-maximum incidence fell from 1.7394% to 0.3595%. This counts 8-bit values equal to 255, not sensor highlight recovery.

RGB output means changed from [80.51275424299568, 58.87389730301538, 53.30851386966471] to [73.17891821027794, 52.561851087185396, 47.875210853120045]; the shoulder/WB/colour changes intentionally alter appearance and are not a claim of measured camera colour accuracy.

Two repeats per stage. Performance is host-specific. Both RAW file hashes are recorded in the JSON reports.
