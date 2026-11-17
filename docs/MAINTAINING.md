# Maintain one codebase in GitHub and offline GitLab

Only the new private `Zvi-Badash/the-zookeeper` repository is in scope. Git remotes are independent; CI has no cross-repository write operations. Clone it, add your internal GitLab remote, and push the same branches and tags through your approved air-gap transfer process. `git bundle create zookeeper.bundle --all` and `git clone zookeeper.bundle` work without internet. Do not put credentials or imported data in Git.

GitHub pull requests and main pushes run Python tests and a browser test with outbound requests blocked. Version tags additionally build a Linux amd64 GPU image, export/reload it, run a CPU audit with `--network none`, verify persistence across restart, and publish split Docker archives, checksums, source and offline build inputs as GitHub release assets. A failed test/build blocks publication. Runner permissions are read-only for tests; only release publication has contents:write. GPU hardware is not assumed on hosted runners.

The release workflow expects a `vMAJOR.MINOR.PATCH` tag; update application version, compose image tag and release documentation together before tagging. Version 0.1.0's actual initial verification is tracked in TESTING.md.

## Offline GitLab CI runner

Use a Docker executor with a preloaded `zookeeper-ci:0.1.0` test image and `pull_policy: never`. Prepare the image online for the exact lockfiles:

```bash
uv export --frozen --no-dev --group dev --no-emit-project -o requirements-dev.lock
uv pip install --python .venv/bin/python pip==25.1.1
.venv/bin/python -m pip download --only-binary=:all: --require-hashes -r requirements-dev.lock -d wheelhouse-dev
docker build -f Dockerfile.ci -t zookeeper-ci:0.1.0 .
docker save zookeeper-ci:0.1.0 | gzip -1 > zookeeper-ci.tar.gz
```

`Dockerfile.ci` uses the Playwright Ubuntu image with Chromium and its system libraries, installs the locked Python test wheels, and primes `/opt/npm-cache` from the frontend lock. Transfer and `docker load` the CI image on the offline runner. It runs npm with `--offline` and uses no package indexes. Test source mounts from GitLab into the image. The default Python executable is `/opt/venv/bin/python`.

The tag release job uses a dedicated **shell executor** tagged `offline-docker`, with Docker, Node 22, Git and the prepared Python environment on PATH. Preload `zookeeper-buildbase:cuda12.6-py312`, pre-extract the application wheels/requirements into `/opt/zookeeper-inputs`, and seed `/opt/npm-cache` from the CI image. Set `ZK_OFFLINE=1` (the release script reuses the base instead of fetching it). Copy the offline-inputs archive to `/opt/zookeeper-inputs` for the GitLab job. It saves checksummed image files as retained GitLab job artifacts. Configure the GitLab instance's maximum artifact size/storage to accommodate these archives; publication to your internal package registry is an optional local policy choice.

If GitLab has no Docker daemon, run only the test job and build releases on the designated internal Docker builder. No automatic synchronization to GitHub occurs from offline GitLab.
