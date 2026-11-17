#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
version="${1:-0.1.0}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
docker_cli="${DOCKER:-docker}"
out="artifacts/v$version"
mkdir -p "$out"
"$docker_cli" build --platform linux/amd64 --network=none --build-arg "VERSION=$version" -t "the-zookeeper:$version-gpu" .
"$docker_cli" save "the-zookeeper:$version-gpu" | gzip -1 > "$out/zookeeper-gpu-v$version.tar.gz"
"$docker_cli" load -i "$out/zookeeper-gpu-v$version.tar.gz"
bash scripts/container-smoke.sh "the-zookeeper:$version-gpu"
split -b 1800M -d -a 3 "$out/zookeeper-gpu-v$version.tar.gz" "$out/zookeeper-gpu-v$version.tar.gz.part-"
rm "$out/zookeeper-gpu-v$version.tar.gz"
