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

`zookeeper-source-*.tar.gz` includes the complete source and bundled browser assets. `zookeeper-offline-inputs-*.tar.gz` includes hashed Python wheels, including GPU runtime packages. Load `zookeeper-base-*.part-*` for a network-free Docker rebuild. Read docs/OFFLINE.md in the source archive.

The release pipeline runs Python and browser tests, exports and reloads the Docker archive, starts it with `--network none`, audits the demo with exact CPU search, and verifies snapshot, cache and review persistence across restart. The browser test blocks outbound requests and exercises Monaco workers, diff, local Python/JavaScript formatting, all projections and literal code display. A100 performance, exact GPU parity and million-row resource measurements require the deployment host; no production-scale performance claim is made.
