# Photo workbench expansion

## Editing history and portable projects

`web/history.js` stores up to 50 immutable states per photo, including preset and nested settings. Consecutive slider updates coalesce; undo followed by another edit discards that redo branch. Undo/redo buttons, history selection and Ctrl+Z/Ctrl+Shift+Z/Ctrl+Y share the same history. History also covers manual recipes and AI changes. Browser session restoration preserves the cursor.

Save Project downloads a `.z30proj` ZIP containing **original photo copies**, JSON settings, history and analysis. No original is overwritten. Files stay on this computer; the archive itself is sensitive to the extent its photos are sensitive. It can reopen after the server restarts without locating the originals again. Maximum 8 photos, 512 MiB total, 150 MiB per photo and 50 history states per photo.

Projects stream to the browser without a duplicate archive on disk. A short-lived download ticket pins only its input photos; expired unused tickets release those pins. Imports validate version, canonical archive names, counts, sizes, SHA256 and history cursor/settings, then decode; any import error rolls back the newly created photo sessions. Encrypted, linked, undeclared and path-traversal archive entries are rejected. Existing open photos remain unchanged.

Tests: portable original/history roundtrip, corrupt hashes, undeclared paths, future versions, inconsistent history, HTTP save/reopen and source pins; Node tests cover coalescing, baseline preservation, redo branches, bounded size and deep snapshots.

## Native-resolution inspection

The original 100% preview toggle is replaced by a full-resolution inspector. The backend decodes and renders the full image with the same geometry/processing as export, caches its read-only RGB8 output under the existing byte budget, and returns a bounded lossless PNG crop. No resize is used for that crop. A minimap and navigation choose another area. Pixel mapping is 1 image pixel per CSS pixel, subject to OS/device scaling. The first view may take several seconds; new settings invalidate the cache. This verifies focus/noise/sharpening on the actual full render rather than the reduced RAW preview. The HTTP regression compares the returned pixels exactly with the native renderer and tests cache reuse.

## Per-photo AI batch and preferences

Automatic styles include natural, portrait, warm film, product and night. Maximum interpolation strength, exposure delta and colour delta are enforced locally before quality fallback. The personal style text and choices persist in browser localStorage; no API credential is stored. The CLI command/login and JSON schema remain unchanged.

The batch dialog explicitly chooses a maximum number of photo analyses (1–8). Photos are analyzed sequentially, with a common style and the first successful summary as a semantic reference. Every photo receives independent settings and quality fallback, rather than copying one photo's sliders to all others. Editing a queued photo skips it; edits to another photo do not invalidate the batch item. Each completed item gets its own history and AI undo point. Stop cancels the active job and leaves later items unstarted. An uncertain remote cancellation stops the queue, preserving reconnect IDs. This uses existing Codex quota once per started photo; ordinary tests use mocks and do not spend quota. Shared semantic style is not a promise of calibrated colour matching across scenes.

Tests cover local strength/delta bounds, categorical geometry preservation, invalid preferences, sequential execution, request budgets, queued edits and stopping after uncertain network errors.

## Local masks, denoise and manual optics

Linear gradients, feathered radial masks and painted strokes apply local exposure, shadow, temperature and saturation changes in float32, before global tone/gamut mapping. Up to 8 masks and 512 brush points are accepted; enabled/invert/opacity/feather and per-mask controls are editable. Stroke segments use analytic distance with bounded bounding boxes. Mask coordinates are relative to the final composition: review their positions after changing rotation/crop. Every mask edit participates in history, recipe/project saving, preview, native inspector and TIFF16 export.

Separate luminance/chroma noise controls use a small edge-aware weighted neighbourhood in linear RGB after decode. This is conventional denoising, not an AI restoration model. Spatial weights and luminance differences reduce averaging across strong edges; results can still soften texture and cannot restore lost details. Processing uses strips with two-row halos. When optical correction already owns a temporary array, denoise reuses it with preserved original halo rows instead of allocating another full-frame float buffer.

Manual radial correction maps output coordinates back to the source using `1+k1*r²+k2*r⁴`, with radius normalized by the larger half-dimension. Red/blue chromatic-aberration controls add per-channel radial scale in per-mille; vignette compensation is an exposure gain proportional to radius squared. Bilinear sampling stays float32. Out-of-frame regions are black and can be cropped. User profiles save/load this explicit `centered-radial-v1` convention, not Lensfun XML. No lens profile coefficients are fabricated or automatically matched to EXIF. These controls have synthetic geometric tests but **no real lens/grid calibration** supplied.

References for the algorithm families: [Lensfun distortion/TCA descriptions](https://lensfun.github.io/manual/latest/group__Lens.html) and [MIT bilateral-filter references](https://people.csail.mit.edu/sparis/bf/). The implementation is independent NumPy code, with no imported Lensfun database or third-party filter source and no new paid API/model dependencies.

Synthetic tests cover disabled identity, mask support/inversion, exact chunk-boundary agreement, reduced flat-field noise with edge preservation, chroma-only luminance preservation, in-place halo equality, channel-specific optics and genuine >256-level uint16 output. Preview and full-size denoise work at different sampling resolutions; evaluate final texture in the native inspector. Quality fallback changes only the nine GPT-editable global controls and preserves manually configured masks/optics/denoise.
