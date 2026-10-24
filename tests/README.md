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

Benchmarks use `tools/benchmark_pipeline.py --isolated`. Each stage and repeat runs in a fresh child process; the OS peak RSS includes that stage's prerequisite decode/render, not a Python-only allocation count. The earlier P0 JSON files explicitly retain the original cumulative-process method. Timing is wall-clock median. Compare paired reports with the same methodology and do not infer universal speedups from these samples.

P1 adds monotonicity, black anchoring, shoulder continuity, tone-table approximation error, warm-hue preservation, gamut mapping, chunk-boundary sharpening, RGB16 TIFF tags / ICC / unique levels, real 14-bit RAW TIFF readback, HTTP export and session-expiry tests. Advisor lifecycle races use blocked fake processes and late-result publication checks, preserving all original CLI tests without spending quota.

The GitHub Actions matrix runs generated-fixture tests on Windows and Linux. Actual RAW checks are explicitly skipped in CI unless `Z30_RAW_SAMPLES` is supplied; no CI pass claims those files were tested there.

P2 brings the suite to 63 tests. `test_quality_cache.py` verifies bounded byte eviction, session purge, identical-render cache hits, reasonable full-strength adjustments, harmful suggestion fallback, unchanged composition and active-source advice filtering. `test_export_jobs.py` verifies immutable snapshots, ZIP content, failed-item-only retry, persisted interruption recovery, checksum rejection, cancellation publication, owner locks and storage/path limits. HTTP batch tests close both photo sessions before downloading two valid outputs. Advisor tests verify the result filter runs before publication. All 11 original CLI contract tests are retained.

Interactive verification additionally imported the public 12/14-bit NEFs, queued both with quality protection, removed a photo while exporting, and downloaded a ZIP containing two 3712×5568 ICC JPEGs. These were generated locally, with no Codex model call. Only the small benchmark comparison thumbnails and an interface screenshot are committed, not the source NEFs or durable input snapshots.

The one-click automatic grading feature brings Python coverage to 67 methods and adds 11 Node tests (`node --test tests/auto-grade.test.cjs`). HTTP tests reuse a fake CLI process but run the actual schema validation, quality guard and renderer. Node tests exercise the production revision/source guard, late cancellation before a job ID arrives, cancellation during rendering, failed cancellation, bad results, timeout and repeat-click protection. CI executes both suites. A separate live-model/browser verification, when performed, is recorded in `docs/auto-grade.md`; ordinary automated tests do not call a model.

The reliability optimization suite now has **74 Python methods and 15 Node tests**. Added regressions exercise low-quota streaming ZIP/per-file access, failed-task retrieval and download pins, idempotent analysis identity, bounded read retries/recovery without model resubmission, eight-photo refresh restoration, lease expiry/ownership/heartbeat, high-frequency clipping and native-preview quality fallback. The 74-method local run includes both real-RAW methods; CI skips those two when the external sample directory is absent.

The expanded workbench has **89 Python methods and 25 Node tests**, including the two optional real-RAW methods in the local run. `test_projects.py` and HTTP cases check original hashes, bounded portable archives, rollback, history and analysis restoration, pinned streaming downloads and exact native crop/cache reuse. `test_automation.py` and `batch-grade.test.cjs` check local style limits, unchanged spatial settings, per-photo budgets, shared reference, cancellation and queued edits. `test_spatial.py` checks mask support, strip/halo equality, noise variance/edge behaviour, manual per-channel optics, no input mutation and uint16 precision. History tests cover nested snapshots, current-state-preserving browser compaction, discrete strokes/rotation and slider coalescing.

Run all browser-controller unit tests with `node --test tests/*.test.cjs`. Browser project save, actual server restart/reopen, native pixel sizing, painted masks and two real CLI batch analyses are separately documented in `docs/workbench.md`; CI does not reproduce those live model calls.
