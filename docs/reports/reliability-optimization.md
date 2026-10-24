# Reliability optimizations

These changes address four independently reproduced review findings. Original photos remain unchanged; existing Codex CLI login and model access are retained.

## 1. Export retrieval and disk usage

`export_jobs.py`, `server.py`, `web/app.js`: completed files are downloadable individually in any task state. ZIP is generated directly into the HTTP stream, without a second stored copy or a full ZIP memory buffer. Download readers pin only their own job; queue metadata operations do not hold a lock for the entire network transfer. Retry/delete cannot invalidate an active download, and disconnected readers release their pin.

Regression: two 4096-byte outputs within a 12000-byte task budget now complete and can be retrieved in a valid streamed ZIP, although storing another ZIP copy would exceed that budget. Legacy jobs marked failed still expose completed files. HTTP tests read the connection-framed ZIP and each individual image. No input snapshots or user originals are removed by this optimization.

## 2. Recoverable analysis identity

`codex_assist.py`, `server.py`, `web/auto-grade.js`, `web/app.js`: each automatic click has a random request ID bound to source, parameters, goal and mode. Repeating a submission with that identity returns the original job, including completed/cancelled jobs; changed parameters are rejected. Request lookup restores a lost submission response without starting a model request. Transient GET failures retry at most twice, then request remote cancellation. Unconfirmed cancellation exposes a reconnect-only action, preventing an accidental new model submission. History is capped at 256 records per process instead of silently evicting idempotency records.

Regressions cover duplicate HTTP submissions, source-scoped lookup, parameter conflicts, cancellation identity, lost responses, transient polling recovery, bounded failure/cancellation and reconnect without a second model job. Neither retries nor regression tests invoke an additional live model.

## 3. Page-owned photo sessions

`server.py`, `web/app.js`: the browser tab keeps a random client ID in sessionStorage. Refresh restores the same photos and locally saved parameters/AI undo point, rather than allocating another set of invisible sessions. Pending analysis IDs survive refresh but are recovered as reviewable results, never silently auto-applied after a new page load. Photo access and analysis lookup check page ownership. Heartbeats every 30 seconds retain active sessions; a maintenance thread reclaims sessions unused for 30 minutes, their cache, temporary inputs and analysis jobs. Each tab allows 8 photos and the process has a 32-photo bound.

HTTP regressions restore eight photos and parameters, remove one and successfully import another, and verify expiration cancels jobs/deletes only temporary copies while another tab's heartbeat and ownership remain intact. Browser storage must be available for refresh recovery. Restarting the backend still removes temporary photos; users must reimport originals and load their downloaded recipes. This does not silently retain originals on disk indefinitely.
