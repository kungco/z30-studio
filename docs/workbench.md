# Photo workbench expansion

## Editing history and portable projects

`web/history.js` stores up to 50 immutable states per photo, including preset and nested settings. Consecutive slider updates coalesce; undo followed by another edit discards that redo branch. Undo/redo buttons, history selection and Ctrl+Z/Ctrl+Shift+Z/Ctrl+Y share the same history. History also covers manual recipes and AI changes. Browser session restoration preserves the cursor.

Save Project downloads a `.z30proj` ZIP containing **original photo copies**, JSON settings, history and analysis. No original is overwritten. Files stay on this computer; the archive itself is sensitive to the extent its photos are sensitive. It can reopen after the server restarts without locating the originals again. Maximum 8 photos, 512 MiB total, 150 MiB per photo and 50 history states per photo.

Projects stream to the browser without a duplicate archive on disk. A short-lived download ticket pins only its input photos; expired unused tickets release those pins. Imports validate version, canonical archive names, counts, sizes, SHA256 and history cursor/settings, then decode; any import error rolls back the newly created photo sessions. Encrypted, linked, undeclared and path-traversal archive entries are rejected. Existing open photos remain unchanged.

Tests: portable original/history roundtrip, corrupt hashes, undeclared paths, future versions, inconsistent history, HTTP save/reopen and source pins; Node tests cover coalescing, baseline preservation, redo branches, bounded size and deep snapshots.

## Native-resolution inspection

The original 100% preview toggle is replaced by a full-resolution inspector. The backend decodes and renders the full image with the same geometry/processing as export, caches its read-only RGB8 output under the existing byte budget, and returns a bounded lossless PNG crop. No resize is used for that crop. A minimap and navigation choose another area. Pixel mapping is 1 image pixel per CSS pixel, subject to OS/device scaling. The first view may take several seconds; new settings invalidate the cache. This verifies focus/noise/sharpening on the actual full render rather than the reduced RAW preview. The HTTP regression compares the returned pixels exactly with the native renderer and tests cache reuse.
