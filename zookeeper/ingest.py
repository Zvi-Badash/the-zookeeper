"""Batch Parquet ingestion, with original text retained on disk."""

import json
import time
import uuid
from collections import Counter

import boto3
import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config

from .storage import language

REQUIRED = {"content", "split", "label", "embedding", "size", "hash", "path"}


def new_snapshot(store, origin):
    sid = uuid.uuid4().hex
    store.path(sid).mkdir()
    with store.db() as c:
        c.execute(
            "INSERT INTO snapshots VALUES(?,?,?,'importing',NULL)",
            (sid, origin, time.time()),
        )
    return sid


def s3_client():
    import os

    endpoint = os.environ.get("MINIO_ENDPOINT")
    if not endpoint:
        raise ValueError("MINIO_ENDPOINT is not configured on the server")
    if not os.environ.get("MINIO_ACCESS_KEY") or not os.environ.get("MINIO_SECRET_KEY"):
        raise ValueError(
            "MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be configured on the server"
        )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("MINIO_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("MINIO_SECRET_KEY"),
        region_name=os.environ.get("MINIO_REGION", "us-east-1"),
        verify=os.environ.get("MINIO_CA") or True,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3},
        ),
    )


def import_minio(store, sid, bucket, key, progress):
    progress(0.01, "Downloading Parquet from MinIO")
    s3_client().download_file(bucket, key, str(store.path(sid) / "input.parquet"))
    return ingest(store, sid, store.path(sid) / "input.parquet", progress)


def ingest(store, sid, path, progress):
    pf = pq.ParquetFile(path)
    missing = REQUIRED - set(pf.schema_arrow.names)
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))
    n = pf.metadata.num_rows
    if not n:
        raise ValueError("Parquet has no rows")
    label_type = pf.schema_arrow.field("label").type
    if pa.types.is_string(label_type) or pa.types.is_large_string(label_type):
        label_sql = "VARCHAR"
    elif pa.types.is_boolean(label_type):
        label_sql = "BOOLEAN"
    elif pa.types.is_integer(label_type):
        label_sql = "HUGEINT" if pa.types.is_unsigned_integer(label_type) else "BIGINT"
    elif pa.types.is_floating(label_type):
        label_sql = "DOUBLE"
    else:
        raise ValueError("label must contain two scalar text, number or boolean values")
    et = pf.schema_arrow.field("embedding").type
    if not (
        pa.types.is_list(et)
        or pa.types.is_large_list(et)
        or pa.types.is_fixed_size_list(et)
    ):
        raise ValueError("embedding must be a numeric list column")
    if not (pa.types.is_integer(et.value_type) or pa.types.is_floating(et.value_type)):
        raise ValueError("embedding must contain numeric vectors")
    # Discover dimension without scanning source text or computing a full-file fingerprint.
    dim = None
    for batch in pf.iter_batches(batch_size=2048, columns=["embedding"]):
        for vector in batch.column(0).to_pylist():
            if vector is not None and len(vector):
                dim = len(vector)
                break
        if dim:
            break
    if not dim:
        raise ValueError("No embedding dimension can be inferred")
    root = store.path(sid)
    vectors = np.memmap(
        root / "vectors.f32", mode="w+", dtype="float32", shape=(n, dim)
    )
    counts, excluded, labels = Counter(), Counter(), set()
    c = duckdb.connect(str(root / "rows.duckdb"))
    c.execute("SET threads=4")
    c.execute(
        f"CREATE TABLE rows(row_id BIGINT, content VARCHAR, split VARCHAR, label {label_sql}, size DOUBLE, hash VARCHAR, path VARCHAR, language VARCHAR, source VARCHAR, repository VARCHAR, usable BOOLEAN, embedding_state VARCHAR, extra JSON)"
    )
    offset = 0
    try:
        for batch in pf.iter_batches(batch_size=1024):
            rows = []
            for record in batch.to_pylist():
                rid = offset + len(rows)
                for field in ["content", "split", "path"]:
                    if not isinstance(record[field], str):
                        raise ValueError(f"Row {rid}: {field} must be text")
                if (
                    record["label"] is None
                    or record["label"] == ""
                    or not record["split"]
                    or (
                        isinstance(record["label"], float)
                        and not np.isfinite(record["label"])
                    )
                ):
                    raise ValueError(f"Row {rid}: label and split must be nonempty")
                if (
                    record["size"] is None
                    or not np.isfinite(float(record["size"]))
                    or float(record["size"]) < 0
                ):
                    raise ValueError(
                        f"Row {rid}: size must be a finite nonnegative number"
                    )
                if record["hash"] is not None and not isinstance(record["hash"], str):
                    raise ValueError(f"Row {rid}: hash must be text or null")
                raw = record["embedding"]
                state = "missing"
                if raw is not None and len(raw):
                    if len(raw) != dim:
                        raise ValueError(
                            f"Row {rid}: inconsistent embedding dimensions ({len(raw)} vs {dim})"
                        )
                    v = np.asarray(raw, dtype=np.float32)
                    norm = np.linalg.norm(v.astype(np.float64))
                    if not np.isfinite(v).all() or not np.isfinite(norm):
                        state = "nonfinite"
                    elif norm == 0:
                        state = "zero"
                    else:
                        vectors[rid] = v / norm
                        state = "usable"
                if state != "usable":
                    vectors[rid] = 0
                    excluded[(record["split"], state)] += 1
                counts[state] += 1
                labels.add(record["label"])
                extra = {
                    k: v
                    for k, v in record.items()
                    if k not in REQUIRED
                    and isinstance(v, (str, int, float, bool, type(None)))
                    and (not isinstance(v, float) or np.isfinite(v))
                }
                rows.append(
                    {
                        **{
                            k: record[k]
                            for k in [
                                "content",
                                "split",
                                "label",
                                "size",
                                "hash",
                                "path",
                            ]
                        },
                        "row_id": rid,
                        "language": language(record["path"], record.get("language")),
                        "source": str(record.get("source") or "unknown"),
                        "repository": str(record.get("repository") or "unknown"),
                        "usable": state == "usable",
                        "embedding_state": state,
                        "extra": json.dumps(extra),
                    }
                )
            if len(labels) > 2:
                raise ValueError(
                    "Expected two dataset class values; found more than two labels"
                )
            table = pa.Table.from_pylist(rows)
            c.register("batch_rows", table)
            c.execute("INSERT INTO rows BY NAME SELECT * FROM batch_rows")
            c.unregister("batch_rows")
            offset += len(rows)
            progress(0.05 + 0.88 * offset / n, f"Validated {offset:,} / {n:,} rows")
        if len(labels) != 2:
            raise ValueError("Expected exactly two dataset class values")
        vectors.flush()
        c.execute("CREATE INDEX row_id_idx ON rows(row_id)")
        summary = {
            "rows": n,
            "dimension": dim,
            "embeddings": dict(counts),
            "labels": sorted(labels),
            "scalar_fields": [
                f.name
                for f in pf.schema_arrow
                if f.name not in REQUIRED
                and (
                    pa.types.is_string(f.type)
                    or pa.types.is_integer(f.type)
                    or pa.types.is_floating(f.type)
                    or pa.types.is_boolean(f.type)
                )
            ],
            "excluded_by_split": [
                {"split": s, "state": reason, "count": count}
                for (s, reason), count in sorted(excluded.items())
            ],
        }
        for field in ["split", "label", "language"]:
            summary[field] = dict(
                c.execute(
                    f"SELECT {field}, count(*) FROM rows GROUP BY {field} ORDER BY {field}"
                ).fetchall()
            )
        summary["size_quantiles"] = c.execute(
            "SELECT quantile_cont(size, [0, .1, .5, .9, 1]) FROM rows"
        ).fetchone()[0]
        summary["size_histogram"] = [
            {
                "range": f"{lo}–{hi}",
                "count": c.execute(
                    "SELECT count(*) FROM rows WHERE size >= ? AND size < ?", [lo, hi]
                ).fetchone()[0],
            }
            for lo, hi in [(0, 1000), (1000, 10000), (10000, 100000), (100000, 1e300)]
        ]
    finally:
        c.close()
        del vectors
    with store.db() as state:
        state.execute(
            "UPDATE snapshots SET status='ready',summary=? WHERE id=?",
            (json.dumps(summary), sid),
        )
    progress(0.95, "Snapshot ready; computing initial PCA")
    from .projections import project

    project(store, sid, "pca", lambda p, m: progress(0.95 + 0.05 * p, m))
    return {"snapshot": sid, "summary": summary}
