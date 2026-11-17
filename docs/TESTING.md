# Verification record

Automated tests cover batch import and schema errors, preserved unusable rows, planted near-copies and exact hashes, score cutoff counts, opposite-label retrieval despite a closer same-label neighbour, self-match exclusion, reverse-pair deduplication, independent persisted reviews, reopened snapshots after restart, S3-compatible import/reuse/refresh, all three projections, language inference, Python formatting and parse failure, cohort distances, and missing/no-match/low-score distinctions.

The browser smoke test uses production assets with all requests outside loopback aborted. It exercises local upload, Monaco single/diff editors and workers, Python and JavaScript formatting, parse failures, language override, literal untrusted markup, coverage representatives, projection switching, an atlas click event and saved review reopening. The atlas click event is injected through Plotly's event API; physical WebGL hit testing still deserves a target-browser acceptance check.

The release script builds and reloads its Docker archive, runs CPU audits with container networking disabled, and verifies snapshot/audit/review persistence after a container restart. The optional CUDA test compares GPU and CPU cosine scores and skips when no CUDA device is available.

Initial validation (2026-11-17, Linux x86-64 / Python 3.12): Python tests and the offline Chromium smoke test passed. The persistent IVF-PQ test also passed on 1,201 synthetic rows, including numeric labels, exact reranking, sparse reference filtering, self exclusion and reopening the stored index. Runtime browser dependencies report zero npm audit advisories. The application image builds with Docker networking disabled from the supplied wheels and base image.

Final Python run: **11 passed, 2 skipped** in 24 seconds. The skips are the optional real-MinIO fixture and GPU parity test. Production browser smoke: **1 passed**, with all outbound browser requests blocked.

The exported GPU archive was loaded successfully, the container started with networking disabled, CPU demo audits completed, and snapshot/audit/review state survived a container restart. GitHub-hosted CI has not run at initial handoff because OAuth workflow-upload permission is pending; the identical build/test scripts were run locally.

The default S3-compatible import fixture uses Moto's mocked S3 protocol. A real MinIO fixture test is provided in `test_minio_integration.py` (`ZK_TEST_MINIO=http://127.0.0.1:19000` with its documented synthetic fixture credentials). It was not run here: Docker Hub and Quay denied access to the historical MinIO image, and the official archived binary returned HTTP 410. No claim of a real MinIO integration pass is made.

Outstanding deployment checks: actual internal MinIO credentials/private CA, authenticated proxy, NVIDIA A100 driver/runtime visibility, exact GPU/CPU parity on that host, memory/capacity handling under competing GPU workloads, and representative large-corpus build/search/projection timing and peak disk/RAM/VRAM. No full-scale benchmark is claimed.
