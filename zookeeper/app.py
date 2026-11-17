"""One FastAPI service, one persistent volume, one background worker."""

import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import black
import numpy as np
import pyarrow as pa
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ingest import import_minio, ingest, new_snapshot
from .projections import cohort_distances, project
from .search import Search, audit, cache_key
from .storage import LANGUAGES, Store, row_dicts, where_scope

META = "row_id,path,split,label,language,size,hash,source,repository,usable,embedding_state"


class MinioInput(BaseModel):
    bucket: str = Field(min_length=1, max_length=255)
    key: str = Field(min_length=1, max_length=4096)
    refresh: bool = False


class AuditInput(BaseModel):
    source: dict = Field(default_factory=dict)
    reference: dict = Field(default_factory=dict)
    mode: str = "auto"
    opposite: bool = False
    nprobe: int = Field(default=32, ge=1, le=4096)
    sample: int = Field(default=0, ge=0, le=10000)


class ReviewInput(BaseModel):
    a: int = Field(ge=0)
    b: int = Field(ge=0)
    workflow: str
    decision: str
    notes: str = Field(default="", max_length=20000)


def create_app(data_root=None, assets=None):
    store = Store(data_root or os.environ.get("ZK_DATA", "data"))

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.pool.shutdown(wait=True)

    app = FastAPI(
        title="The Zookeeper", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.store = store

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Plotly uses inline style, Monaco uses workers and generated style sheets.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'self'"
        )
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "languages": LANGUAGES}

    @app.get("/api/snapshots")
    def snapshots():
        with store.db() as c:
            return row_dicts(
                c.execute(
                    "SELECT id,origin,created,status FROM snapshots ORDER BY created DESC"
                )
            )

    @app.get("/api/snapshots/{sid}")
    def snapshot(sid):
        return store.snapshot(sid)

    @app.post("/api/import/minio")
    def minio(body: MinioInput):
        origin = json.dumps([body.bucket, body.key])
        if not body.refresh:
            with store.db() as c:
                row = c.execute(
                    "SELECT id FROM snapshots WHERE origin=? AND status='ready' ORDER BY created DESC LIMIT 1",
                    (origin,),
                ).fetchone()
            if row:
                return {"snapshot": row[0], "reused": True}
        sid = new_snapshot(store, origin)
        jid = store.submit(
            "import", sid, lambda p: import_minio(store, sid, body.bucket, body.key, p)
        )
        return {"snapshot": sid, "job": jid}

    @app.post("/api/import/upload")
    def upload(file: UploadFile = File(...)):
        sid = new_snapshot(store, "Local Parquet upload")
        path = store.path(sid) / "input.parquet"
        # UploadFile is spooled; copy in bounded chunks instead of reading into RAM.
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        return {
            "snapshot": sid,
            "job": store.submit("import", sid, lambda p: ingest(store, sid, path, p)),
        }

    @app.get("/api/jobs")
    def jobs():
        with store.db() as c:
            return row_dicts(
                c.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")
            )

    @app.get("/api/jobs/{jid}")
    def job(jid):
        with store.db() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found")
        result = dict(row)
        result["result"] = json.loads(result["result"] or "null")
        return result

    @app.get("/api/snapshots/{sid}/rows")
    def rows(
        sid,
        scope: str = "{}",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        text: str = "",
        hash: str | None = None,
    ):
        clause, params = where_scope(json.loads(scope))
        if text:
            clause += " AND (contains(path,?) OR contains(content,?))"
            params += [text, text]
        if hash is not None:
            clause += " AND hash=?"
            params.append(hash)
        with store.catalog(sid) as c:
            count = c.execute(
                f"SELECT count(*) FROM rows WHERE {clause}", params
            ).fetchone()[0]
            items = row_dicts(
                c.execute(
                    f"SELECT {META} FROM rows WHERE {clause} ORDER BY row_id LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                )
            )
        return {"count": count, "items": items}

    @app.get("/api/snapshots/{sid}/rows/{rid}")
    def row(sid, rid: int, full: bool = False):
        with store.catalog(sid) as c:
            selected = "content" if full else "substring(content,1,200000) AS content"
            items = row_dicts(
                c.execute(
                    f"SELECT {META}, {selected},length(content) AS original_characters,extra FROM rows WHERE row_id=?",
                    [rid],
                )
            )
        if not items:
            raise HTTPException(404, "Row not found")
        result = items[0]
        result["partial"] = len(result["content"]) < result["original_characters"]
        return result

    @app.get("/api/snapshots/{sid}/rows/{rid}/download")
    def download(sid, rid: int):
        result = row(sid, rid, True)
        return Response(
            result["content"],
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="source-{rid}.txt"'},
        )

    @app.post("/api/format/python")
    def format_python(body: dict):
        content = body.get("content", "")
        if not isinstance(content, str) or len(content) > 2_000_000:
            raise ValueError("Formatting supports at most 2 million characters")
        try:
            # format_str never discovers dataset configuration, imports, or plugins.
            return {
                "content": black.format_str(
                    content,
                    mode=black.Mode(
                        line_length=88, target_versions={black.TargetVersion.PY312}
                    ),
                )
            }
        except (black.InvalidInput, TokenError, SyntaxError) as exc:
            raise ValueError(
                "Python could not be parsed; original source is unchanged"
            ) from exc

    @app.post("/api/snapshots/{sid}/audits")
    def run_audit(sid, body: AuditInput):
        store.snapshot(sid)
        if body.mode not in {"auto", "exact_cpu", "exact_gpu", "approximate"}:
            raise ValueError("Unknown search mode")
        where_scope(body.source)
        where_scope(body.reference)
        return {
            "job": store.submit(
                "audit", sid, lambda p: audit(store, sid, body.model_dump(), p)
            )
        }

    def load_audit(sid, key):
        if len(key) != 32 or any(ch not in "0123456789abcdef" for ch in key):
            raise ValueError("Invalid audit key")
        root = store.path(sid) / "audits"
        if not (root / (key + ".json")).exists():
            raise ValueError("Audit not found")
        return root / (key + ".parquet"), json.loads(
            (root / (key + ".json")).read_text()
        )

    @app.get("/api/snapshots/{sid}/audits")
    def audits(sid):
        return [
            json.loads(p.read_text())
            for p in sorted((store.path(sid) / "audits").glob("*.json"))
        ]

    def register_review(c, sid, workflow):
        with store.db() as db:
            data = [
                dict(r)
                for r in db.execute(
                    "SELECT a,b,decision,notes FROM reviews WHERE snapshot=? AND workflow=?",
                    (sid, workflow),
                ).fetchall()
            ]
        schema = pa.schema(
            [
                ("a", pa.int64()),
                ("b", pa.int64()),
                ("decision", pa.string()),
                ("notes", pa.string()),
            ]
        )
        c.register("review", pa.Table.from_pylist(data, schema=schema))

    @app.get("/api/snapshots/{sid}/audits/{key}")
    def audit_results(
        sid,
        key,
        cutoff: float = Query(0.95, ge=-1, le=1),
        low: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        scope: str = "{}",
        review_status: str = "",
        sort: str = "similarity",
    ):
        path, meta = load_audit(sid, key)
        scope_sql, params = where_scope(json.loads(scope), "s")
        workflow = "label" if meta["settings"]["opposite"] else "leakage"
        with store.catalog(sid) as c:
            c.read_parquet(str(path)).create_view("audit")
            register_review(c, sid, workflow)
            matched = c.execute(
                "SELECT count(*) FROM audit WHERE state='matched'"
            ).fetchone()[0]
            bins = row_dicts(
                c.execute(
                    "SELECT least(39,greatest(0,CAST(floor((similarity+1)*20) AS INTEGER))) AS bin,count(*) AS count FROM audit WHERE state='matched' GROUP BY bin ORDER BY bin"
                )
            )
            total_above = c.execute(
                "SELECT count(*) FROM audit WHERE similarity>=?", [cutoff]
            ).fetchone()[0]
            base = "audit"
            if meta["settings"]["opposite"]:
                base = "(SELECT * FROM audit WHERE state='matched' QUALIFY row_number() OVER(PARTITION BY least(a,b),greatest(a,b) ORDER BY a)=1)"
            joins = f"FROM {base} m JOIN rows s ON s.row_id=m.a JOIN rows t ON t.row_id=m.b LEFT JOIN review r ON r.a=least(m.a,m.b) AND r.b=greatest(m.a,m.b)"
            clause = f"m.similarity {'<' if low else '>='} ? AND {scope_sql}"
            params = [cutoff, *params]
            if review_status:
                clause += " AND coalesce(r.decision,'unreviewed')=?"
                params.append(review_status)
            count = c.execute(
                f"SELECT count(*) {joins} WHERE {clause}", params
            ).fetchone()[0]
            ordering = {
                "similarity": "m.similarity",
                "path": "s.path",
                "size": "s.size",
            }.get(sort, "m.similarity")
            items = row_dicts(
                c.execute(
                    f"SELECT m.*,s.path,s.split,s.label,s.language,s.size,t.path AS match_path,t.split AS match_split,t.label AS match_label,t.language AS match_language,t.size AS match_size,coalesce(r.decision,'unreviewed') AS review_status {joins} WHERE {clause} ORDER BY {ordering} {'ASC' if low else 'DESC'},m.a LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                )
            )
        return {
            **meta,
            "count": count,
            "matched": matched,
            "above_cutoff": total_above,
            "percentage_population": 100 * total_above / max(1, meta["population"]),
            "percentage_searchable": 100 * total_above / max(1, meta["searchable"]),
            "histogram": bins,
            "items": items,
        }

    @app.get("/api/snapshots/{sid}/audits/{key}/coverage")
    def coverage(
        sid,
        key,
        field: str = "language",
        cutoff: float = Query(0.6, ge=-1, le=1),
        scope: str = "{}",
        sort: str = "median",
    ):
        if field not in {"label", "language", "source", "size", "text_length"}:
            raise ValueError("Unknown slice field")
        path, meta = load_audit(sid, key)
        measure = "length(s.content)" if field == "text_length" else "s.size"
        group = (
            f"CASE WHEN {measure}<1000 THEN '0–999' WHEN {measure}<10000 THEN '1000–9999' WHEN {measure}<100000 THEN '10000–99999' ELSE '100000+' END"
            if field in {"size", "text_length"}
            else f"s.{field}"
        )
        clause, params = where_scope(json.loads(scope), "s")
        reference_scope = {k: v for k, v in json.loads(scope).items() if k != "split"}
        reference_scope.update(meta["settings"]["reference"])
        reference_clause, reference_params = where_scope(reference_scope, "s")
        ordering = {
            "median": "median ASC NULLS FIRST",
            "p10": "p10 ASC NULLS FIRST",
            "sample_size": "sample_size DESC",
            "fraction_below": "fraction_below DESC NULLS LAST",
        }.get(sort, "median ASC NULLS FIRST")
        with store.catalog(sid) as c:
            c.read_parquet(str(path)).create_view("audit")
            items = row_dicts(
                c.execute(
                    f"SELECT e.*,coalesce(t.reference_rows,0) AS reference_rows,coalesce(t.reference_searchable,0) AS reference_searchable FROM (SELECT {group} AS slice,count(*) AS sample_size,count(m.similarity) AS valid_matches,median(m.similarity) AS median,quantile_cont(m.similarity,.1) AS p10,count(*) FILTER(WHERE m.state='missing_embedding') AS missing_embedding,count(*) FILTER(WHERE m.state='no_match') AS no_match,count(*) FILTER(WHERE m.similarity<?)*1.0/nullif(count(m.similarity),0) AS fraction_below FROM audit m JOIN rows s ON s.row_id=m.a WHERE {clause} GROUP BY 1) e LEFT JOIN (SELECT {group} AS slice,count(*) AS reference_rows,count(*) FILTER(WHERE usable) AS reference_searchable FROM rows s WHERE {reference_clause} GROUP BY 1) t USING(slice) ORDER BY {ordering}",
                    [cutoff, *params, *reference_params],
                )
            )
        return {
            "population": meta["population"],
            "reference_scope": reference_scope,
            "items": items,
        }

    def register_conflicts(c, sid, key, cutoff, scope, review_status):
        from .groups import conflict_membership

        path, meta = load_audit(sid, key)
        if not meta["settings"]["opposite"]:
            raise ValueError("Conflict groups require an opposite-label audit")
        c.read_parquet(str(path)).create_view("audit")
        register_review(c, sid, "label")
        clause, params = where_scope(json.loads(scope), "s")
        if review_status:
            clause += " AND coalesce(r.decision,'unreviewed')=?"
            params.append(review_status)
        edges = c.execute(
            f"SELECT least(m.a,m.b) AS a,greatest(m.a,m.b) AS b,max(m.similarity) AS similarity FROM audit m JOIN rows s ON s.row_id=m.a LEFT JOIN review r ON r.a=least(m.a,m.b) AND r.b=greatest(m.a,m.b) WHERE m.state='matched' AND m.similarity>=? AND {clause} GROUP BY 1,2",
            [cutoff, *params],
        ).fetch_arrow_table()
        c.register("conflict_edges", edges)
        c.register("members", conflict_membership(edges))

    @app.get("/api/snapshots/{sid}/audits/{key}/groups")
    def conflict_groups(
        sid,
        key,
        cutoff: float = Query(0.95, ge=-1, le=1),
        scope: str = "{}",
        review_status: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        with store.catalog(sid) as c:
            register_conflicts(c, sid, key, cutoff, scope, review_status)
            count = c.execute(
                "SELECT count(DISTINCT group_id) FROM members"
            ).fetchone()[0]
            items = row_dicts(
                c.execute(
                    "SELECT g.group_id,count(*) AS rows,count(DISTINCT s.label) AS label_count,string_agg(DISTINCT cast(s.label AS VARCHAR),', ' ORDER BY cast(s.label AS VARCHAR)) AS labels,min(s.path) AS example_path,p.pair_count,p.strongest,p.weakest FROM members g JOIN rows s ON s.row_id=g.row_id JOIN (SELECT g.group_id,count(*) AS pair_count,max(e.similarity) AS strongest,min(e.similarity) AS weakest FROM conflict_edges e JOIN members g ON g.row_id=e.a GROUP BY g.group_id) p USING(group_id) GROUP BY g.group_id,p.pair_count,p.strongest,p.weakest ORDER BY p.strongest DESC,rows DESC,g.group_id LIMIT ? OFFSET ?",
                    [limit, offset],
                )
            )
        return {
            "count": count,
            "items": items,
            "meaning": "Groups connect cached opposite-label neighbour links above the cutoff; not every pair in a group is necessarily above the cutoff.",
        }

    @app.get("/api/snapshots/{sid}/audits/{key}/groups/{gid}")
    def conflict_group(
        sid,
        key,
        gid: int,
        cutoff: float = Query(0.95, ge=-1, le=1),
        scope: str = "{}",
        review_status: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        with store.catalog(sid) as c:
            register_conflicts(c, sid, key, cutoff, scope, review_status)
            count = c.execute(
                "SELECT count(*) FROM members WHERE group_id=?", [gid]
            ).fetchone()[0]
            if not count:
                raise HTTPException(404, "Conflict group not found at this cutoff")
            items = row_dicts(
                c.execute(
                    f"SELECT {','.join('s.'+f for f in META.split(','))} FROM members g JOIN rows s ON s.row_id=g.row_id WHERE g.group_id=? ORDER BY s.label,s.row_id LIMIT ? OFFSET ?",
                    [gid, limit, offset],
                )
            )
            pairs = row_dicts(
                c.execute(
                    "SELECT e.*,s.path,s.label,t.path AS match_path,t.label AS match_label,coalesce(r.decision,'unreviewed') AS review_status FROM conflict_edges e JOIN members g ON g.row_id=e.a JOIN rows s ON s.row_id=e.a JOIN rows t ON t.row_id=e.b LEFT JOIN review r ON r.a=e.a AND r.b=e.b WHERE g.group_id=? ORDER BY e.similarity DESC,e.a LIMIT 50",
                    [gid],
                )
            )
        return {"count": count, "items": items, "pairs": pairs}

    @app.get("/api/snapshots/{sid}/audits/{key}/representatives")
    def representatives(sid, key, scope: str = "{}"):
        path, _ = load_audit(sid, key)
        clause, params = where_scope(json.loads(scope), "s")
        with store.catalog(sid) as c:
            c.read_parquet(str(path)).create_view("audit")
            query = f"SELECT m.*,s.path,s.label,s.language,t.path AS match_path,t.label AS match_label FROM audit m JOIN rows s ON s.row_id=m.a JOIN rows t ON t.row_id=m.b WHERE {clause}"
            count, median = c.execute(
                f"SELECT count(*),median(similarity) FROM ({query})", params
            ).fetchone()
            low = row_dicts(
                c.execute(query + " ORDER BY m.similarity,m.a LIMIT 3", params)
            )
            middle = row_dicts(
                c.execute(
                    query + " ORDER BY abs(m.similarity-?),m.a LIMIT 3",
                    [*params, median or 0],
                )
            )
        unique = {r["a"]: r for r in [*low, *middle]}
        return {"count": count, "items": list(unique.values())}

    @app.get("/api/snapshots/{sid}/hashes")
    def hashes(
        sid,
        source: str = "test",
        reference: str = "train",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        with store.catalog(sid) as c:
            clause = "hash IS NOT NULL AND hash<>'' AND split IN (?,?)"
            query = f"SELECT hash,count(*) AS rows,count(*) FILTER(WHERE split=?) AS source_rows,count(*) FILTER(WHERE split=?) AS reference_rows FROM rows WHERE {clause} GROUP BY hash HAVING source_rows>0 AND reference_rows>0"
            count = c.execute(
                f"SELECT count(*) FROM ({query})",
                [source, reference, source, reference],
            ).fetchone()[0]
            groups = row_dicts(
                c.execute(
                    query + " ORDER BY source_rows DESC,hash LIMIT ? OFFSET ?",
                    [source, reference, source, reference, limit, offset],
                )
            )
            for g in groups:
                g["examples"] = row_dicts(
                    c.execute(
                        f"SELECT {META} FROM rows WHERE hash=? AND split IN (?,?) ORDER BY split,row_id LIMIT 20",
                        [g["hash"], source, reference],
                    )
                )
                g["a"] = c.execute(
                    "SELECT row_id FROM rows WHERE hash=? AND split=? LIMIT 1",
                    [g["hash"], source],
                ).fetchone()[0]
                g["b"] = c.execute(
                    "SELECT row_id FROM rows WHERE hash=? AND split=? LIMIT 1",
                    [g["hash"], reference],
                ).fetchone()[0]
        return {"count": count, "items": groups}

    @app.get("/api/snapshots/{sid}/reviews")
    def reviews(sid, a: int, b: int, workflow: str):
        with store.db() as c:
            row = c.execute(
                "SELECT * FROM reviews WHERE snapshot=? AND a=? AND b=? AND workflow=?",
                (sid, min(a, b), max(a, b), workflow),
            ).fetchone()
        return dict(row) if row else {"decision": "unreviewed", "notes": ""}

    @app.put("/api/snapshots/{sid}/reviews")
    def save_review(sid, body: ReviewInput):
        import time

        allowed = {
            "leakage": {
                "unreviewed",
                "confirmed leakage",
                "related but acceptable",
                "distinct",
                "unsure",
            },
            "label": {"unreviewed", "suspected mislabel", "labels justified", "unsure"},
        }
        if (
            body.workflow not in allowed
            or body.decision not in allowed[body.workflow]
            or body.a == body.b
        ):
            raise ValueError("Invalid pair review")
        with store.catalog(sid) as c:
            if (
                c.execute(
                    "SELECT count(*) FROM rows WHERE row_id IN (?,?)", [body.a, body.b]
                ).fetchone()[0]
                != 2
            ):
                raise ValueError("Row not found")
        with store.db() as c:
            c.execute(
                "INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,?)",
                (
                    sid,
                    min(body.a, body.b),
                    max(body.a, body.b),
                    body.workflow,
                    body.decision,
                    body.notes,
                    time.time(),
                ),
            )
        return {"saved": True}

    @app.get("/api/snapshots/{sid}/rows/{rid}/neighbours")
    def neighbours(sid, rid: int, scope: str = "{}", k: int = Query(10, ge=1, le=50)):
        source = row(sid, rid)
        if not source["usable"]:
            return {"state": "missing_embedding", "items": []}
        scores, ids, mode = Search(store, sid).query(
            np.asarray([rid]), json.loads(scope), k=k
        )
        items = []
        with store.catalog(sid) as c:
            for score, match in zip(scores[0], ids[0]):
                if match >= 0:
                    item = row_dicts(
                        c.execute(
                            f"SELECT {META} FROM rows WHERE row_id=?", [int(match)]
                        )
                    )[0]
                    items.append(
                        {
                            **item,
                            "similarity": float(score),
                            "opposite_label": item["label"] != source["label"],
                        }
                    )
        return {"mode": mode, "items": items}

    @app.post("/api/snapshots/{sid}/projections/{method}")
    def projection(sid, method):
        if method not in {"pca", "umap", "tsne"}:
            raise ValueError("Unknown projection")
        return {
            "job": store.submit(
                "projection", sid, lambda p: project(store, sid, method, p)
            )
        }

    @app.get("/api/snapshots/{sid}/projections/{method}")
    def points(
        sid,
        method,
        scope: str = "{}",
        limit: int = Query(15000, ge=1, le=30000),
        audit_key: str = "",
    ):
        if method not in {"pca", "umap", "tsne"}:
            raise ValueError("Unknown projection")
        path = store.path(sid) / f"{method}.npz"
        if not path.exists():
            raise HTTPException(404, "Projection has not been fitted")
        data = np.load(path)
        clause, params = where_scope(json.loads(scope))
        with store.catalog(sid) as c:
            c.register(
                "projected",
                pa.table(
                    {
                        "row_id": data["ids"],
                        "x": data["xyz"][:, 0],
                        "y": data["xyz"][:, 1],
                        "z": data["xyz"][:, 2],
                    }
                ),
            )
            count = c.execute(
                f"SELECT count(*) FROM rows JOIN projected USING(row_id) WHERE {clause}",
                params,
            ).fetchone()[0]
            # Deterministic bounded sample. Only metadata and coordinates leave the server.
            items = row_dicts(
                c.execute(
                    f"SELECT {','.join('rows.'+f for f in META.split(','))},extra,x,y,z FROM rows JOIN projected USING(row_id) WHERE {clause} ORDER BY hash(rows.row_id) LIMIT ?",
                    [*params, limit],
                )
            )
            if audit_key:
                ap, _ = load_audit(sid, audit_key)
                lookup = dict(
                    c.execute(
                        "SELECT a,similarity FROM read_parquet(?)", [str(ap)]
                    ).fetchall()
                )
                for item in items:
                    item["training_similarity"] = lookup.get(item["row_id"])
        return {
            "projection_rows": len(data["ids"]),
            "filtered_rows": count,
            "shown": len(items),
            "items": items,
        }

    @app.post("/api/snapshots/{sid}/cohorts")
    def cohorts(sid, body: dict):
        return cohort_distances(
            store,
            sid,
            body.get("left", {}),
            body.get("right", {}),
            max(1, min(int(body.get("sample", 10000)), 100000)),
        )

    assets = Path(assets or Path(__file__).parent.parent / "frontend" / "dist")
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="browser")
    return app


from tokenize import TokenError
