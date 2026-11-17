"""Synthetic *source code*, not downloaded data or model embeddings."""

import hashlib
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


def generate(path):
    snippets = [
        (
            "train",
            "safe",
            "sum.py",
            "def add(a,b):\n return a+b\n",
            [1, 0, 0, 0],
            "core",
        ),
        (
            "test",
            "safe",
            "sum_copy.py",
            "def add(a,b):\n return a+b\n",
            [1, 0, 0, 0],
            "core",
        ),
        (
            "test",
            "safe",
            "sum_short.py",
            "def add(a,b): return a+b\n",
            [0.999, 0.025, 0, 0],
            "core",
        ),
        (
            "train",
            "unsafe",
            "shell.js",
            "function run(x){return eval(x)}\n",
            [0, 1, 0, 0],
            "web",
        ),
        (
            "test",
            "unsafe",
            "shell_test.js",
            "const run=(x)=>eval(x);\n",
            [0.01, 0.999, 0, 0],
            "web",
        ),
        (
            "test",
            "safe",
            "rare.rs",
            'fn main() { println!("rare"); }\n',
            [0, 0, 1, 0],
            "novel",
        ),
        (
            "test",
            "safe",
            "rare2.rs",
            "fn uncommon() -> u32 { 7 }\n",
            [0, 0, 0.99, 0.05],
            "novel",
        ),
        (
            "train",
            "safe",
            "guard.py",
            "def allowed(user):\n return user.is_admin\n",
            [0.7, 0.7, 0, 0],
            "auth",
        ),
        (
            "train",
            "safe",
            "guard_closest.py",
            "def allowed(user): return user.is_admin\n",
            [0.7001, 0.7, 0, 0],
            "auth",
        ),
        (
            "train",
            "unsafe",
            "guard_conflict.py",
            "def allowed(user): return True\n",
            [0.7002, 0.7, 0, 0],
            "auth",
        ),
        (
            "validation",
            "safe",
            "ok.go",
            "package main\nfunc ok() bool { return true }\n",
            [0.9, 0.1, 0, 0],
            "core",
        ),
        (
            "test",
            "safe",
            "missing.weird",
            "<script>globalThis.pwned=true</script>\n",
            None,
            None,
        ),
        (
            "train",
            "safe",
            "missing-copy.weird",
            "<script>globalThis.pwned=true</script>\n",
            [0, 0, 0, 0],
            None,
        ),
        (
            "test",
            "unsafe",
            "broken.py",
            "def (broken:\n",
            [float("nan"), 0, 0, 0],
            None,
        ),
        (
            "train",
            "safe",
            "hello.java",
            "class Hello { int x = 1; }\n",
            [0.5, 0.1, 0.1, 0.7],
            "jvm",
        ),
        (
            "validation",
            "unsafe",
            "config.yaml",
            "debug: true\n",
            [0.1, 0.8, 0.2, 0],
            "web",
        ),
    ]
    rows = []
    for split, label, name, content, embedding, source in snippets:
        rows.append(
            {
                "content": content,
                "split": split,
                "label": label,
                "embedding": embedding,
                "size": len(content.encode()),
                "hash": hashlib.sha256(content.encode()).hexdigest(),
                "path": name,
                "source": source,
                "repository": "demo" if source else None,
            }
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="?", default="data/demo.parquet")
    generate(p.parse_args().path)
