"""Publish checksummed assets in parallel; safely resume by server SHA-256 digest."""

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import time
from pathlib import Path


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("--repo", default="Zvi-Badash/the-zookeeper")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    tag = "v" + args.version
    root = Path("artifacts") / tag
    releases = json.loads(gh("api", f"repos/{args.repo}/releases?per_page=100"))
    release = next((r for r in releases if r["tag_name"] == tag), None)
    if release is None:
        gh(
            "release",
            "create",
            tag,
            "--repo",
            args.repo,
            "--target",
            "main",
            "--draft",
            "--title",
            "The Zookeeper " + args.version,
            "--notes-file",
            "docs/RELEASE.md",
        )
        releases = json.loads(gh("api", f"repos/{args.repo}/releases?per_page=100"))
        release = next(r for r in releases if r["tag_name"] == tag)
    remote = {}
    for page in range(1, 100):
        items = json.loads(
            gh(
                "api",
                f'repos/{args.repo}/releases/{release["id"]}/assets?per_page=100&page={page}',
            )
        )
        remote.update({a["name"]: a for a in items})
        if len(items) < 100:
            break
    assets = []
    for line in (root / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.strip().removeprefix("./")
        path = root / name
        if path.parent != root:
            raise ValueError("Unexpected asset path")
        with path.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()
        if actual != digest:
            raise ValueError("Checksum mismatch: " + name)
        if (
            remote.get(name, {}).get("digest") == "sha256:" + digest
            and remote[name]["state"] == "uploaded"
        ):
            print("Already verified: " + name, flush=True)
        else:
            assets.append(path)

    def upload(path):
        for attempt in range(3):
            result = subprocess.run(
                [
                    "gh",
                    "release",
                    "upload",
                    tag,
                    str(path),
                    "--repo",
                    args.repo,
                    "--clobber",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("Uploaded: " + path.name, flush=True)
                return
            if attempt == 2:
                raise RuntimeError(path.name + ": " + result.stderr)
            time.sleep(2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(upload, p) for p in assets}
        while pending:
            done, pending = concurrent.futures.wait(
                pending, timeout=20, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                future.result()
            if pending:
                print(f"{len(pending)} assets remaining", flush=True)
    upload(root / "SHA256SUMS")
    if args.publish:
        gh(
            "release",
            "edit",
            tag,
            "--repo",
            args.repo,
            "--draft=false",
            "--notes-file",
            "docs/RELEASE.md",
        )
    print(
        gh(
            "release", "view", tag, "--repo", args.repo, "--json", "url", "--jq", ".url"
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
