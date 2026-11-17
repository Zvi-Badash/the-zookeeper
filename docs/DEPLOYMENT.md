# Deploy to Ubuntu 24.04 / x86-64

1. Install Docker Engine and NVIDIA Container Toolkit on the target before air-gapping it, or transfer the packages through your normal internal provisioning. Validate the **actual host driver** with `nvidia-smi`. The host CUDA display reports driver capability, not the userspace libraries selected by this image.
2. Copy every release asset, run `sha256sum -c SHA256SUMS`, then join `zookeeper-gpu-v0.1.0.tar.gz.part-*` in numeric order and pipe to `docker load`. No pull or package install is needed on the target.
3. Extract the source archive. Copy `.env.example` to `.env`, set MinIO endpoint/credentials, and protect the file with `chmod 600 .env`. The endpoint is fixed on the server; users enter only bucket/key. Credentials need GetObject on the relevant prefix, not write access. Mount a private CA read-only and set `MINIO_CA` to its in-container path.
4. Start CPU mode with `docker compose up -d`, or GPU mode with `docker compose -f compose.yaml -f compose.gpu.yaml up -d`. Compose uses `pull_policy: never`. Docker creates the named volume with the image's nonroot ownership. For a bind mount, provision the directory with uid/gid 10001 and restrict host access. Back up the whole volume while the app is stopped; it contains originals, vectors, indexes, audit caches, projections, SQLite reviews and jobs.
5. Proxy the loopback port through the team's authenticated internal reverse proxy. Enable authentication on **all** paths, including `/api`; permit large uploads and long import progress polling. Source text, metadata and notes are untrusted text. Preserve the app's Content Security Policy. TLS is required for clipboard support in browsers other than localhost.

GPU preflight after `docker load`:

```bash
docker run --rm --gpus all the-zookeeper:0.1.0-gpu nvidia-smi
docker run --rm --gpus all the-zookeeper:0.1.0-gpu python -c 'import cupy as cp; print(cp.cuda.runtime.driverGetVersion()); print(cp.cuda.runtime.runtimeGetVersion()); print(cp.asnumpy(cp.ones((2,2)) @ cp.ones((2,2))))'
docker exec zookeeper-zookeeper-1 python -m zookeeper.demo /data/demo.parquet
```

Container names depend on the Compose project name; use `docker compose ps`. Download the demo to the browser machine with `docker cp <container>:/data/demo.parquet ./demo.parquet`, then upload it in the Dataset screen. Alternatively put it in internal MinIO and open that bucket/key. Run test → train in exact CPU and exact GPU modes and compare the ranked demo scores. Use `uv run --group gpu pytest -q -k gpu` on a prepared test runner with device access for automated parity.

CUDA 12.6 is chosen deliberately. NVIDIA documents a CUDA 12.x minor compatibility floor of Linux driver 525.60.13; CUDA 12.6.3's toolkit driver is 560.35.05. Prefer a current supported datacenter driver with A100 support; verify the actual driver rather than relying on the reported “13.3”. Newer drivers ordinarily support older CUDA userspace, but deployment must pass the commands above. Sources: [NVIDIA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html), [12.6.3 release notes](https://docs.nvidia.com/cuda/archive/12.6.3/cuda-toolkit-release-notes/index.html).

Exact GPU search uses a bounded memory budget (default 8 GiB) and reference shards. Select **exact CPU** when there is no GPU, or after an explicit GPU capacity/runtime error. The app does not silently mark a failed GPU search as exact GPU. `ZK_GPU_BUDGET_MIB` reduces the allocation budget. See architecture notes before attempting 1.5M × 4096.
