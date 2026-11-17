#!/usr/bin/env bash
# Repackage source/build inputs without repeating an unchanged image build.
set -euo pipefail
cd "$(dirname "$0")/.."
version="${1:-0.1.0}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
out="artifacts/v$version"
mkdir -p "$out"
git archive HEAD > "$out/source.tar"
tar -rf "$out/source.tar" frontend/dist
if [[ -z "$(git ls-files .github/workflows)" ]]; then tar -rf "$out/source.tar" .github; fi
gzip -1 < "$out/source.tar" > "$out/zookeeper-source-v$version.tar.gz"
rm "$out/source.tar"
git bundle create "$out/zookeeper-history-v$version.bundle" --all
tar --exclude='*.chunks' --exclude='*.part' --exclude='_logs' -czf "$out/zookeeper-offline-inputs-v$version.tar.gz" wheelhouse requirements.lock requirements-dev.lock npm-cache
cp docs/RELEASE.md "$out/RELEASE.md"
(cd "$out" && sha256sum ./*.part-* ./*.tar.gz ./*.bundle RELEASE.md > SHA256SUMS)
