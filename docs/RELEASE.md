The Zookeeper 0.1.0 — offline source-code dataset audits

Image: `the-zookeeper:0.1.0-gpu` (`linux/amd64`, Ubuntu 24.04, Python 3.12, CUDA 12.6 userspace). It also runs on CPU without NVIDIA device access.

Download all release assets to one empty directory. Verify before loading:

```bash
sha256sum -c SHA256SUMS
# Join numbered parts in lexicographic (000, 001, ...) order:
cat zookeeper-gpu-v0.1.0.tar.gz.part-* | docker load
docker run -d --name zookeeper -p 127.0.0.1:8000:8000 -v zookeeper-data:/data the-zookeeper:0.1.0-gpu
```

For GPU operation add `--gpus all` after validating the NVIDIA host driver and Container Toolkit. See the source README and docs/DEPLOYMENT.md. A reported host “CUDA 13.3” is not a measured driver compatibility check. Validate CuPy and compare exact CPU/GPU scores on the target A100.

`zookeeper-source-*.tar.gz` includes the complete source and bundled browser assets. Join `zookeeper-offline-inputs-*.tar.gz.part-*` in ascending suffix order and extract the stream to obtain hashed Python wheels, GPU packages, test dependencies and the npm cache. GPU-image and input-kit parts are 32 MiB each (the final part is smaller), making uploads resumable. Load `zookeeper-base-*.part-*` for a network-free Docker rebuild. Read docs/OFFLINE.md in the source archive.

The release pipeline runs Python and browser tests, exports and reloads the Docker archive, starts it with `--network none`, audits the demo with exact CPU search, and verifies snapshot, cache and review persistence across restart. The browser test blocks outbound requests and exercises Monaco workers, diff, local Python/JavaScript formatting, all projections and literal code display. A100 performance, exact GPU parity and million-row resource measurements require the deployment host; no production-scale performance claim is made.

Local verification passed (15 Python tests passed, one optional real-MinIO check skipped), including exact GPU/CPU score parity and GPU reference-shard boundary checks on an RTX 3050. The offline browser smoke and image reload/CPU/persistent-volume checks passed. This build includes conflict-group cards, character-length coverage slices, reference counts and a paginated low-coverage queue. Real internal MinIO/private CA and standard NVIDIA container startup on the A100 remain deployment checks. GitHub and offline GitLab pipelines are included; see docs/CI_STATUS.md for GitHub's zero-job startup failures observed during handoff.
