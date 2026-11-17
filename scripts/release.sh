#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
version="${1:-0.1.0}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo 'Expected a semantic version' >&2; exit 2; fi
docker_cli="${DOCKER:-docker}"
out="artifacts/v$version"
mkdir -p "$out"
if [[ "${ZK_OFFLINE:-0}" == 1 ]]; then
  "$docker_cli" image inspect zookeeper-buildbase:cuda12.6-py312 >/dev/null
else
  "$docker_cli" build --platform linux/amd64 -f Dockerfile.base -t zookeeper-buildbase:cuda12.6-py312 .
fi
"$docker_cli" build --platform linux/amd64 --network=none --build-arg "VERSION=$version" -t "the-zookeeper:$version-gpu" .
# Export and reload the exact archive that will be distributed.
"$docker_cli" save "the-zookeeper:$version-gpu" | gzip -1 > "$out/zookeeper-gpu-v$version.tar.gz"
"$docker_cli" load -i "$out/zookeeper-gpu-v$version.tar.gz"
bash scripts/container-smoke.sh "the-zookeeper:$version-gpu"
"$docker_cli" save zookeeper-buildbase:cuda12.6-py312 | gzip -1 > "$out/zookeeper-base-v$version.tar.gz"
# Full source plus the built, locally bundled browser assets.
git archive HEAD > "$out/source.tar"
tar -rf "$out/source.tar" frontend/dist
if [[ -z "$(git ls-files .github/workflows)" ]]; then
  # Retain complete CI sources in the source handoff even if OAuth workflow
  # scope still prevents uploading these files to the remote repository.
  tar -rf "$out/source.tar" .github
fi
gzip -1 < "$out/source.tar" > "$out/zookeeper-source-v$version.tar.gz"
rm "$out/source.tar"
tar --exclude='*.chunks' --exclude='*.part' -czf "$out/zookeeper-offline-inputs-v$version.tar.gz" wheelhouse requirements.lock
for archive in "$out/zookeeper-gpu-v$version.tar.gz" "$out/zookeeper-base-v$version.tar.gz"; do
  split -b 1800M -d -a 3 "$archive" "$archive.part-"
  rm "$archive"
done
cp docs/RELEASE.md "$out/RELEASE.md"
(cd "$out" && sha256sum ./*.part-* ./*.tar.gz RELEASE.md > SHA256SUMS)
echo "Release assets are in $out"
