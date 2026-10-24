# Reliability optimizations

These changes address four independently reproduced review findings. Original photos remain unchanged; existing Codex CLI login and model access are retained.

## 1. Export retrieval and disk usage

`export_jobs.py`, `server.py`, `web/app.js`: completed files are downloadable individually in any task state. ZIP is generated directly into the HTTP stream, without a second stored copy or a full ZIP memory buffer. Download readers pin only their own job; queue metadata operations do not hold a lock for the entire network transfer. Retry/delete cannot invalidate an active download, and disconnected readers release their pin.

Regression: two 4096-byte outputs within a 12000-byte task budget now complete and can be retrieved in a valid streamed ZIP, although storing another ZIP copy would exceed that budget. Legacy jobs marked failed still expose completed files. HTTP tests read the connection-framed ZIP and each individual image. No input snapshots or user originals are removed by this optimization.
