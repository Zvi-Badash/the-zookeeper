"""Download compatible wheels from uv.lock; verify every byte against its lock hash.

Run with the prepared Python 3.12 environment. This is an alternative to
pip download when a builder's pip connection is unusually slow.
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default="requirements.lock")
    parser.add_argument("--destination", default="wheelhouse")
    args = parser.parse_args()
    names = set(
        re.findall(r"^([a-zA-Z0-9_.-]+)==", Path(args.requirements).read_text(), re.M)
    )
    ranks = {tag: i for i, tag in enumerate(sys_tags())}
    lock = tomllib.loads(Path("uv.lock").read_text())
    destination = Path(args.destination)
    destination.mkdir(exist_ok=True)
    chosen = []
    for package in lock["package"]:
        if package["name"] not in names or package["name"] == "colorama":
            continue
        candidates = []
        for wheel in package.get("wheels", []):
            filename = unquote(urlparse(wheel["url"]).path.rsplit("/", 1)[-1])
            tags = parse_wheel_filename(filename)[3]
            rank = min((ranks[t] for t in tags if t in ranks), default=None)
            if rank is not None:
                candidates.append((rank, filename, wheel))
        if not candidates:
            raise RuntimeError("No compatible wheel for " + package["name"])
        _, filename, wheel = min(candidates, key=lambda x: x[0])
        chosen.append((filename, wheel))

    def fetch(item):
        filename, wheel = item
        target = destination / filename
        expected = wheel["hash"].removeprefix("sha256:")
        if (
            target.exists()
            and hashlib.file_digest(target.open("rb"), "sha256").hexdigest() == expected
        ):
            return filename
        partial = target.with_suffix(".part")
        curl = [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "3",
        ]
        if wheel["size"] > 8 * 1024**2:
            prefix = partial.stat().st_size if partial.exists() else 0
            chunks = destination / (filename + ".chunks")
            chunks.mkdir(exist_ok=True)
            spans = [
                (start, min(start + 2 * 1024**2, wheel["size"]))
                for start in range(prefix, wheel["size"], 2 * 1024**2)
            ]

            def segment(span):
                start, end = span
                p = chunks / str(start)
                if not p.exists() or p.stat().st_size != end - start:
                    subprocess.run(
                        [
                            *curl,
                            "--range",
                            f"{start}-{end-1}",
                            wheel["url"],
                            "--output",
                            str(p),
                        ],
                        check=True,
                    )
                if p.stat().st_size != end - start:
                    raise RuntimeError("Range response has wrong length")

            with ThreadPoolExecutor(max_workers=16) as segments:
                list(segments.map(segment, spans))
            with partial.open("ab") as output:
                for start, _ in spans:
                    with (chunks / str(start)).open("rb") as source:
                        shutil.copyfileobj(source, output)
        else:
            subprocess.run([*curl, wheel["url"], "--output", str(partial)], check=True)
        with partial.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError("Checksum mismatch: " + filename)
        partial.replace(target)
        print("Verified " + filename, flush=True)
        return filename

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, chosen))
    print(f"{len(chosen)} compatible wheels verified", flush=True)


if __name__ == "__main__":
    main()
