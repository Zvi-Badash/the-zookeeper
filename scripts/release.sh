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
bash scripts/release-image.sh "$version"
"$docker_cli" save zookeeper-buildbase:cuda12.6-py312 | gzip -1 > "$out/zookeeper-base-v$version.tar.gz"
for archive in "$out/zookeeper-base-v$version.tar.gz"; do
  split -b 1800M -d -a 3 "$archive" "$archive.part-"
  rm "$archive"
done
bash scripts/package-source.sh "$version"
echo "Release assets are in $out"
