"""Shared scoped Faiss retrieval, float32 reranking and bounded exact GPU search."""

import hashlib
import json
import os
from pathlib import Path

import faiss
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .storage import where_scope

faiss.omp_set_num_threads(int(os.environ.get("ZK_SEARCH_THREADS", "4")))
SEARCH_VERSION = 1


def cache_key(settings):
    return hashlib.sha256(
        json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]


def population(store, sid, scope, usable=True):
    clause, params = where_scope(scope)
    with store.catalog(sid) as c:
        return np.asarray(
            [
                r[0]
                for r in c.execute(
                    f"SELECT row_id FROM rows WHERE {clause} {'AND usable' if usable else ''} ORDER BY row_id",
                    params,
                ).fetchall()
            ],
            dtype=np.int64,
        )


class Search:
    def __init__(self, store, sid):
        self.store, self.sid = store, sid
        self.vectors = store.vectors(sid)
        self.index_dir = store.path(sid) / "indexes"
        self.index_dir.mkdir(exist_ok=True)

    def index(self, ids, scope, approximate):
        with self.store.lock:
            return self._index(ids, scope, approximate)

    def _index(self, ids, scope, approximate):
        dim = self.vectors.shape[1]
        kind = "ivfpq" if approximate and len(ids) >= 1000 else "flat"
        path = self.index_dir / (
            cache_key({"scope": scope, "kind": kind, "version": SEARCH_VERSION})
            + ".faiss"
        )
        if path.exists():
            return faiss.read_index(str(path)), kind
        if kind == "ivfpq":
            m = max(i for i in range(1, min(64, dim) + 1) if dim % i == 0)
            nlist = min(4096, max(16, int(np.sqrt(len(ids)))))
            index = faiss.IndexIVFPQ(
                faiss.IndexFlatIP(dim), dim, nlist, m, 8, faiss.METRIC_INNER_PRODUCT
            )
            sample = np.random.default_rng(42).choice(
                ids, min(len(ids), max(10000, nlist * 40)), replace=False
            )
            index.train(np.ascontiguousarray(self.vectors[sample]))
        else:
            index = faiss.IndexFlatIP(dim)
        index = faiss.IndexIDMap2(index)
        for start in range(0, len(ids), 4096):
            chunk = ids[start : start + 4096]
            index.add_with_ids(np.ascontiguousarray(self.vectors[chunk]), chunk)
        temp = path.with_suffix(".tmp")
        faiss.write_index(index, str(temp))
        temp.replace(path)
        return index, kind

    def query(
        self,
        source_ids,
        reference_scope,
        mode="auto",
        k=1,
        nprobe=32,
        progress=lambda p, m: None,
    ):
        ref = population(self.store, self.sid, reference_scope)
        scores = np.full((len(source_ids), k), np.nan, dtype=np.float32)
        matches = np.full((len(source_ids), k), -1, dtype=np.int64)
        if not len(ref):
            return scores, matches, "exact_cpu"
        if mode == "exact_gpu":
            return self.gpu_query(source_ids, ref, k, progress)
        approximate = mode == "approximate" or (mode == "auto" and len(ref) > 50000)
        index, kind = self.index(ref, reference_scope, approximate)
        actual = "approximate_reranked" if kind == "ivfpq" else "exact_cpu"
        if kind == "ivfpq":
            faiss.ParameterSpace().set_index_parameter(
                index, "nprobe", min(nprobe, faiss.extract_index_ivf(index).nlist)
            )
        for start in range(0, len(source_ids), 128):
            ids = source_ids[start : start + 128]
            # Search extra candidates; exclude self and rerank in original normalised float32 space.
            _, candidates = index.search(
                np.ascontiguousarray(self.vectors[ids]), min(len(ref), max(64, k + 1))
            )
            for j, rid in enumerate(ids):
                cand = candidates[j]
                cand = cand[(cand >= 0) & (cand != rid)]
                if len(cand):
                    sim = np.clip(self.vectors[cand] @ self.vectors[rid], -1, 1)
                    order = np.lexsort((cand, -sim))[:k]
                    matches[start + j, : len(order)] = cand[order]
                    scores[start + j, : len(order)] = sim[order]
            progress(
                (start + len(ids)) / max(1, len(source_ids)),
                f"Searching {start+len(ids):,} / {len(source_ids):,} rows ({actual})",
            )
        return scores, matches, actual

    def gpu_query(self, source_ids, ref, k, progress):
        try:
            import cupy as cp

            cp.cuda.Device(0).use()
            free, _ = cp.cuda.runtime.memGetInfo()
        except Exception as exc:
            raise ValueError(
                "Exact GPU search unavailable. Check NVIDIA driver/container toolkit and GPU wheels; select exact_cpu to continue."
            ) from exc
        # Shard references and query batches: never assume all 22.9 GiB fits on the GPU.
        dim = self.vectors.shape[1]
        budget = min(
            int(free * 0.45), int(os.environ.get("ZK_GPU_BUDGET_MIB", "8192")) * 1024**2
        )
        if budget < max(dim * 4 * 32, 16 * 1024**2):
            raise ValueError(
                "Insufficient free GPU memory; choose exact_cpu or free GPU memory"
            )
        batch = 64
        shard_size = max(1, min(65536, budget // (dim * 4 + batch * 4)))
        all_scores = np.full((len(source_ids), k), np.nan, np.float32)
        all_ids = np.full((len(source_ids), k), -1, np.int64)
        try:
            for start in range(0, len(source_ids), batch):
                qids = source_ids[start : start + batch]
                q = cp.asarray(self.vectors[qids])
                best_s = np.full((len(qids), k), -np.inf, np.float32)
                best_i = np.full((len(qids), k), -1, np.int64)
                for rs in range(0, len(ref), shard_size):
                    rids = ref[rs : rs + shard_size]
                    rv = cp.asarray(self.vectors[rids])
                    sim = q @ rv.T
                    for j, rid in enumerate(qids):
                        same = np.flatnonzero(rids == rid)
                        if len(same):
                            sim[j, int(same[0])] = -cp.inf
                    take = min(k, len(rids))
                    order = cp.argsort(sim, axis=1)[:, -take:][:, ::-1]
                    ss = cp.asnumpy(cp.take_along_axis(sim, order, axis=1))
                    ii = rids[cp.asnumpy(order)]
                    combined_s = np.concatenate([best_s, ss], axis=1)
                    combined_i = np.concatenate([best_i, ii], axis=1)
                    order_cpu = np.argsort(-combined_s, axis=1, kind="stable")[:, :k]
                    best_s = np.take_along_axis(combined_s, order_cpu, axis=1)
                    best_i = np.take_along_axis(combined_i, order_cpu, axis=1)
                    del rv, sim, order
                invalid = ~np.isfinite(best_s)
                best_i[invalid] = -1
                best_s[invalid] = np.nan
                all_scores[start : start + len(qids)] = np.clip(best_s, -1, 1)
                all_ids[start : start + len(qids)] = best_i
                del q
                progress(
                    (start + len(qids)) / max(1, len(source_ids)),
                    "Exact GPU reference shards",
                )
        except cp.cuda.memory.OutOfMemoryError as exc:
            raise ValueError(
                "GPU capacity exceeded; reduce ZK_GPU_BUDGET_MIB or select exact_cpu"
            ) from exc
        finally:
            cp.get_default_memory_pool().free_all_blocks()
        return all_scores, all_ids, "exact_gpu"


def audit(store, sid, settings, progress):
    settings = {
        "source": {},
        "reference": {},
        "mode": "auto",
        "opposite": False,
        "nprobe": 32,
        "sample": 0,
        **settings,
        "version": SEARCH_VERSION,
    }
    key = cache_key(settings)
    root = store.path(sid) / "audits"
    root.mkdir(exist_ok=True)
    output = root / (key + ".parquet")
    meta = root / (key + ".json")
    if output.exists() and meta.exists():
        return json.loads(meta.read_text())
    ids = population(store, sid, settings["source"], usable=False)
    if settings["sample"]:
        ids = np.sort(
            np.random.default_rng(42).choice(
                ids, min(len(ids), settings["sample"]), replace=False
            )
        )
    with store.catalog(sid) as c:
        eligible = set(
            r[0] for r in c.execute("SELECT row_id FROM rows WHERE usable").fetchall()
        )
        labels = (
            dict(c.execute("SELECT row_id,label FROM rows").fetchall())
            if settings["opposite"]
            else {}
        )
    search_ids = np.asarray([rid for rid in ids if rid in eligible], np.int64)
    engine = Search(store, sid)
    pairs, actuals = {}, set()
    groups = sorted(set(labels.values())) if settings["opposite"] else [None]
    for group in groups:
        qids = (
            search_ids
            if group is None
            else np.asarray([i for i in search_ids if labels[i] != group], np.int64)
        )
        ref_scope = dict(settings["reference"])
        if group is not None:
            if (
                ref_scope.get("label") is not None
                and ref_scope.get("label") != ""
                and "label" in ref_scope
                and str(ref_scope["label"]) != str(group)
            ):
                continue
            ref_scope["label"] = group
        if not len(qids):
            continue
        ss, ii, actual = engine.query(
            qids,
            ref_scope,
            settings["mode"],
            nprobe=settings["nprobe"],
            progress=progress,
        )
        actuals.add(actual)
        for rid, score, match in zip(qids, ss[:, 0], ii[:, 0]):
            pairs[int(rid)] = (int(match), float(score) if np.isfinite(score) else None)
    rows = []
    for rid in ids:
        match, score = pairs.get(int(rid), (-1, None))
        rows.append(
            {
                "a": int(rid),
                "b": match if match >= 0 else None,
                "similarity": score,
                "state": (
                    "missing_embedding"
                    if rid not in eligible
                    else "matched" if match >= 0 else "no_match"
                ),
            }
        )
    schema = pa.schema(
        [
            ("a", pa.int64()),
            ("b", pa.int64()),
            ("similarity", pa.float32()),
            ("state", pa.string()),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    temp = output.with_suffix(".tmp")
    pq.write_table(table, temp)
    temp.replace(output)
    result = {
        "key": key,
        "snapshot": sid,
        "settings": settings,
        "modes": sorted(actuals),
        "population": len(ids),
        "searchable": len(search_ids),
        "no_match": sum(r["state"] == "no_match" for r in rows),
    }
    meta.write_text(json.dumps(result))
    return result
