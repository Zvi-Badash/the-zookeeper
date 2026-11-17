# The Zookeeper

An offline web app for scientists auditing source-code datasets with existing embeddings. Import one Parquet object from MinIO or upload a local file; explore an embedding atlas, review cross-split near-copies, find weak training coverage and inspect conflicting labels in a shared Monaco code viewer.

The app uses one Python service, one browser frontend, a local persistent volume, SQLite for jobs/reviews, DuckDB for source and metadata, memory-mapped normalised float32 embeddings, and scoped persistent Faiss indexes. No embedding model or model weights are required. No browser asset uses a CDN.

## Quick start from source (connected build machine)

Python 3.12, Node 22+, and `uv` 0.12.21 (the lockfile writer) are used for builds.

```bash
uv sync --frozen --python 3.12
npm --prefix frontend ci
npm --prefix frontend run build
uv run python -m zookeeper.demo
uv run uvicorn zookeeper.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Open http://127.0.0.1:8000 and upload `data/demo.parquet`. Dataset → snapshot links are stable. Leakage defaults to test → train. Coverage shares the audit cache. Label review searches each opposite-label reference population directly. All paths lead to the same read-only source/diff viewer. Use its review selector to save independent leakage or label decisions.

```bash
uv run pytest -q
cd frontend
npx playwright install --with-deps chromium
npm test
```

The browser test requires the production frontend build. It blocks every request outside loopback. GPU parity skips when CUDA is unavailable.

## Input contract

Required columns: `content`, `split`, `label`, `embedding`, `size`, `hash`, `path`. Content, split and path must be strings; `hash` may be null or empty, `size` is a finite nonnegative number, embeddings are numeric lists with a common dimension. Exactly two nonempty dataset class values (text, numbers or booleans) are expected; their values and the supplied split names are preserved. Optional scalar metadata is retained in `extra` and supports exact-value filtering and atlas colouring; language, source and repository have explicit filter controls. Source/repository default to `unknown`. Language metadata takes precedence over extension inference; unknown languages use plain text and can be overridden in the viewer.

Missing, zero and non-finite vectors are counted separately, excluded from vector search, and retained for source and supplied-hash inspection. Any nonempty vector with a different dimension rejects the snapshot. Input Parquet remains staged on disk; original text, hashes and labels are never rewritten by reviews or formatting.

Reopening the same MinIO bucket/key reuses the most recent completed snapshot. **Refresh snapshot** downloads a new snapshot ID, keeping earlier snapshots and decisions. Local uploads create new snapshots without full-file fingerprinting. Pin MinIO credentials to a read-only policy and use HTTPS with `MINIO_CA` for a private CA.

## Delivery and operation

- [Deployment and NVIDIA preflight](docs/DEPLOYMENT.md)
- [Air-gapped rebuild inputs and procedure](docs/OFFLINE.md)
- [Architecture, scale and resource estimates](docs/ARCHITECTURE.md)
- [GitHub/GitLab maintenance and CI/CD](docs/MAINTAINING.md)
- [Verification and outstanding target-host checks](docs/TESTING.md)
- [Release assets and join order](docs/RELEASE.md)

Run `bash scripts/prepare.sh` on a connected Linux amd64 builder, then `bash scripts/release.sh 0.1.0` to build/export/reload/test both the GPU application and its offline base. Tag pushes run tests and publish checksummed Docker archives as GitHub release assets. The GitLab pipeline uses preloaded offline CI images and publishes its image archives as job artifacts.

Deploy behind an authenticated internal reverse proxy. This release has a shared team review state and no built-in user-account service. Keep the volume, exports and backups restricted. Do not expose the service directly to the public internet.
