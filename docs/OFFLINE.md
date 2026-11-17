# Air-gapped rebuild

Release assets include all **application** rebuild inputs for Linux amd64: complete source, prebuilt browser assets, pinned hashed Python wheels and a fully provisioned Ubuntu/CUDA/Python build base. Docker itself and NVIDIA host drivers/toolkit are host prerequisites provisioned separately.

On the offline builder, verify SHA256SUMS and extract the source and offline-inputs archives into the same empty build directory. Load the base parts in ascending suffix order:

```bash
cat zookeeper-base-v0.1.0.tar.gz.part-* | docker load
tar -xzf zookeeper-source-v0.1.0.tar.gz
cat zookeeper-offline-inputs-v0.1.0.tar.gz.part-* | tar -xzf -
docker build --network=none --platform linux/amd64 -t the-zookeeper:0.1.0-gpu .
docker save the-zookeeper:0.1.0-gpu | gzip -1 > rebuilt-image.tar.gz
```

The Dockerfile installs with `--no-index --require-hashes` from `wheelhouse/` and copies `frontend/dist` directly. It performs no apt operations or frontend downloads. Never rebuild `Dockerfile.base` offline; load the supplied base image. The release pipeline saves that exact built base, so moving apt repositories do not affect offline rebuilds.

To **change** Python code offline, edit the source and rebuild. Browser assets can be rebuilt from the supplied npm cache with Node 22. To add new dependencies, prepare a new package cache on the connected side as described in MAINTAINING.md. The supplied browser bundle works as-is without Node on the target. Source npm lockfiles and Python uv.lock preserve resolved versions; all required runtime browser assets, Monaco workers, language grammars, formatter parsers and font fallbacks are already in frontend/dist. System UI/monospace fonts are used; no remote fonts.

The offline-inputs archive also contains `npm-cache` and `requirements-dev.lock`, with the matching test wheels in `wheelhouse`. With Node 22 and Python 3.12 preinstalled on your build host, browser source and Python tests can be rebuilt without package downloads:

```bash
python3.12 -m venv .venv
.venv/bin/pip install --no-index --find-links=wheelhouse --require-hashes -r requirements-dev.lock
npm --prefix frontend ci --offline --cache npm-cache
npm --prefix frontend run build
.venv/bin/python -m pytest -q
```

Browser tests additionally require the pinned Chromium build and its OS libraries, which the preloaded CI image supplies. The release's Git bundle preserves the repository history for an offline GitLab import; the source archive includes the complete GitHub and GitLab CI configuration.
