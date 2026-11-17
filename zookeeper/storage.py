"""Durable job, snapshot and review state. Source text lives in snapshot DuckDBs."""

import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import numpy as np

EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}
LANGUAGES = sorted(set(EXTENSIONS.values()) | {"plaintext"})
ALIASES = {
    "c#": "csharp",
    "c++": "cpp",
    "bash": "shell",
    "sh": "shell",
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "text": "plaintext",
}


def language(path, explicit=None):
    if explicit:
        candidate = ALIASES.get(str(explicit).lower(), str(explicit).lower())
        return candidate if candidate in LANGUAGES else "plaintext"
    return EXTENSIONS.get(Path(path or "").suffix.lower(), "plaintext")


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-job")
        self.lock = threading.Lock()
        with self.db() as c:
            c.executescript(
                """
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, origin TEXT, created REAL, status TEXT, summary TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, snapshot TEXT, status TEXT, progress REAL, message TEXT, result TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS reviews(snapshot TEXT, a INTEGER, b INTEGER, workflow TEXT, decision TEXT, notes TEXT, updated REAL, PRIMARY KEY(snapshot,a,b,workflow));
            """
            )
            c.execute(
                "UPDATE jobs SET status='interrupted', message='Server restarted; resubmit this job' WHERE status IN ('queued','running')"
            )
            c.execute(
                "UPDATE snapshots SET status='interrupted' WHERE status='importing'"
            )

    def db(self):
        c = sqlite3.connect(self.root / "state.sqlite", timeout=60)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def path(self, sid):
        # IDs never become arbitrary filesystem paths.
        if len(sid) != 32 or any(ch not in "0123456789abcdef" for ch in sid):
            raise ValueError("Invalid snapshot ID")
        return self.root / sid

    def snapshot(self, sid):
        with self.db() as c:
            row = c.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise ValueError("Snapshot not found")
        item = dict(row)
        item["summary"] = json.loads(item["summary"] or "{}")
        return item

    def catalog(self, sid):
        if self.snapshot(sid)["status"] != "ready":
            raise ValueError("Snapshot is not ready")
        c = duckdb.connect()
        c.execute("SET threads=4")
        path = str(self.path(sid) / "rows.duckdb").replace("'", "''")
        c.execute(f"ATTACH '{path}' AS snapshot (READ_ONLY)")
        c.execute("CREATE TEMP VIEW rows AS SELECT * FROM snapshot.rows")
        return c

    def vectors(self, sid):
        s = self.snapshot(sid)["summary"]
        return np.memmap(
            self.path(sid) / "vectors.f32",
            mode="r",
            dtype="float32",
            shape=(s["rows"], s["dimension"]),
        )

    def progress(self, jid, fraction, message):
        with self.db() as c:
            c.execute(
                "UPDATE jobs SET progress=?,message=? WHERE id=?",
                (fraction, message, jid),
            )

    def submit(self, kind, sid, fn):
        jid = uuid.uuid4().hex
        with self.db() as c:
            c.execute(
                "INSERT INTO jobs VALUES(?,?,?,'queued',0,'Waiting',NULL,?)",
                (jid, kind, sid, time.time()),
            )

        def run():
            with self.db() as c:
                c.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
            try:
                result = fn(lambda p, m: self.progress(jid, p, m))
                with self.db() as c:
                    c.execute(
                        "UPDATE jobs SET status='completed',progress=1,message='Complete',result=? WHERE id=?",
                        (json.dumps(result), jid),
                    )
            except Exception as exc:
                # Avoid exception reprs: SDK errors may include endpoint/object information.
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else f"{type(exc).__name__}: job failed; check input and server configuration"
                )
                with self.db() as c:
                    c.execute(
                        "UPDATE jobs SET status='failed',message=? WHERE id=?",
                        (message[:1000], jid),
                    )
                    if kind == "import":
                        c.execute(
                            "UPDATE snapshots SET status='failed' WHERE id=?", (sid,)
                        )

        self.pool.submit(run)
        return jid


SCOPES = {"split", "label", "language", "source", "repository"}


def where_scope(scope, alias=""):
    prefix = alias + "." if alias else ""
    parts, params = [], []
    for key, value in scope.items():
        if value is None or value == "":
            continue
        if key in SCOPES:
            parts.append(f"{prefix}{key} = ?")
        elif key.startswith("extra:") and len(key) > 6:
            parts.append(f"json_extract_string({prefix}extra, ?) = ?")
            params.append("/" + key[6:].replace("~", "~0").replace("/", "~1"))
            value = str(value)
        elif key in {"size_min", "size_max"}:
            parts.append(f"{prefix}size {'>=' if key == 'size_min' else '<'} ?")
        else:
            raise ValueError(f"Unsupported scope field: {key}")
        params.append(value)
    return " AND ".join(parts) or "TRUE", params


def row_dicts(cursor):
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]
