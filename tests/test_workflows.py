import io
import json
import time

import boto3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from zookeeper.app import create_app
from zookeeper.demo import generate
from zookeeper.ingest import ingest, new_snapshot
from zookeeper.search import Search, audit, population
from zookeeper.storage import Store, language


def wait(client, jid):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        j = client.get("/api/jobs/" + jid).json()
        if j["status"] in {"failed", "interrupted"}:
            pytest.fail(j["message"])
        if j["status"] == "completed":
            return j["result"]
        time.sleep(0.03)
    pytest.fail("Job timed out")


@pytest.fixture
def demo(tmp_path):
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        path = generate(tmp_path / "demo.parquet")
        with path.open("rb") as f:
            r = client.post(
                "/api/import/upload", files={"file": ("demo.parquet", f)}
            ).json()
        wait(client, r["job"])
        yield client, r["snapshot"], app.state.store


def run(client, sid, **settings):
    r = client.post(f"/api/snapshots/{sid}/audits", json=settings)
    assert r.status_code == 200, r.text
    return wait(client, r.json()["job"])


def test_local_import_restart_reviews_and_leakage(demo):
    c, sid, store = demo
    base = f"/api/snapshots/{sid}"
    summary = c.get(base).json()["summary"]
    assert summary["rows"] == 16
    assert summary["embeddings"] == {
        "usable": 13,
        "missing": 1,
        "zero": 1,
        "nonfinite": 1,
    }
    r = run(
        c, sid, source={"split": "test"}, reference={"split": "train"}, mode="exact_cpu"
    )
    q = c.get(base + "/audits/" + r["key"] + "?cutoff=.99").json()
    assert (q["population"], q["searchable"], q["no_match"]) == (7, 5, 0)
    assert q["above_cutoff"] == 3
    assert {x["a"] for x in q["items"]} == {1, 2, 4}
    # Changing cutoff reuses the same saved search result and creates no new jobs.
    jobs = len(c.get("/api/jobs").json())
    assert c.get(base + "/audits/" + r["key"] + "?cutoff=1").json()["above_cutoff"] == 1
    assert len(c.get("/api/jobs").json()) == jobs
    hashes = c.get(base + "/hashes").json()
    assert hashes["count"] == 2
    assert any(x["a"] == 11 and x["b"] == 12 for x in hashes["items"])
    review = {
        "a": 1,
        "b": 0,
        "workflow": "leakage",
        "decision": "confirmed leakage",
        "notes": "exact copy",
    }
    assert c.put(base + "/reviews", json=review).status_code == 200
    assert (
        c.get(base + "/reviews?a=0&b=1&workflow=leakage").json()["notes"]
        == "exact copy"
    )
    assert (
        c.get(
            base + "/audits/" + r["key"] + "?review_status=confirmed%20leakage"
        ).json()["count"]
        == 1
    )
    fresh = create_app(store.root)
    with TestClient(fresh) as again:
        assert again.get(base).json()["status"] == "ready"
        assert (
            again.get(base + "/reviews?a=0&b=1&workflow=leakage").json()["decision"]
            == "confirmed leakage"
        )
        assert again.get(base + "/audits").json()[0]["key"] == r["key"]


def test_coverage_and_opposite_label_retrieval(demo):
    c, sid, store = demo
    base = f"/api/snapshots/{sid}"
    r = run(c, sid, source={"split": "test"}, reference={"split": "train"})
    coverage = c.get(
        base + f"/audits/{r['key']}/coverage?field=source&cutoff=.5"
    ).json()
    novel = next(x for x in coverage["items"] if x["slice"] == "novel")
    assert novel["sample_size"] == 2 and novel["fraction_below"] == 1
    unknown = next(x for x in coverage["items"] if x["slice"] == "unknown")
    assert unknown["missing_embedding"] == 2
    reps = c.get(
        base + f"/audits/{r['key']}/representatives",
        params={"scope": json.dumps({"source": "novel"})},
    ).json()
    assert {x["a"] for x in reps["items"]} == {5, 6}
    engine = Search(store, sid)
    _, ids, _ = engine.query(np.array([7]), {"split": "train"})
    assert ids[0, 0] == 8  # closer same-label file
    r = run(
        c,
        sid,
        source={"split": "train"},
        reference={"split": "train"},
        opposite=True,
        mode="exact_cpu",
    )
    q = c.get(base + f"/audits/{r['key']}?cutoff=.99").json()
    assert any({x["a"], x["b"]} == {7, 9} for x in q["items"])
    pairs = [tuple(sorted((x["a"], x["b"]))) for x in q["items"]]
    assert len(pairs) == len(set(pairs))
    assert all(x["a"] != x["b"] and x["label"] != x["match_label"] for x in q["items"])
    review = {
        "a": 7,
        "b": 9,
        "workflow": "label",
        "decision": "suspected mislabel",
        "notes": "check guard",
    }
    assert c.put(base + "/reviews", json=review).status_code == 200
    assert (
        c.get(base + "/reviews?a=9&b=7&workflow=label").json()["decision"]
        == "suspected mislabel"
    )
    assert (
        c.get(base + "/reviews?a=7&b=9&workflow=leakage").json()["decision"]
        == "unreviewed"
    )


def test_no_match_is_not_low_score(demo):
    c, sid, _ = demo
    base = f"/api/snapshots/{sid}"
    r = run(c, sid, source={"split": "test"}, reference={"split": "absent"})
    assert r["no_match"] == 5
    data = c.get(base + f"/audits/{r['key']}/coverage").json()["items"]
    assert sum(x["missing_embedding"] for x in data) == 2
    assert sum(x["no_match"] for x in data) == 5
    assert all(x["median"] is None for x in data)


def test_projections_source_and_formatting(demo):
    c, sid, _ = demo
    base = f"/api/snapshots/{sid}"
    for method in ["pca", "umap", "tsne"]:
        job = c.post(base + "/projections/" + method).json()["job"]
        wait(c, job)
        p = c.get(base + "/projections/" + method).json()
        assert p["shown"] == 13
        assert {x["row_id"] for x in p["items"]} == {
            0,
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            14,
            15,
        }
        assert all("content" not in x for x in p["items"])
    text = c.get(base + "/rows/11").json()
    assert text["language"] == "plaintext" and "<script>" in text["content"]
    original = c.get(base + "/rows/0").json()["content"]
    formatted = c.post("/api/format/python", json={"content": original}).json()[
        "content"
    ]
    assert "def add(a, b):" in formatted
    assert c.get(base + "/rows/0").json()["content"] == original
    assert (
        c.post("/api/format/python", json={"content": "def (bad:"}).status_code == 400
    )
    cohort = c.post(
        base + "/cohorts",
        json={"left": {"split": "test"}, "right": {"split": "train"}, "sample": 100},
    ).json()
    assert cohort["sample_pairs"] == 100 and 0 <= cohort["mean"] <= 2


def test_s3_compatible_import_reuse_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIO_ENDPOINT", "https://s3.amazonaws.com")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "testing")
    monkeypatch.setenv("MINIO_SECRET_KEY", "testing")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="fixture")
        path = generate(tmp_path / "demo.parquet")
        s3.put_object(Bucket="fixture", Key="demo.parquet", Body=path.read_bytes())
        app = create_app(tmp_path / "data")
        with TestClient(app) as c:
            r = c.post(
                "/api/import/minio", json={"bucket": "fixture", "key": "demo.parquet"}
            ).json()
            wait(c, r["job"])
            sid = r["snapshot"]
            r = c.post(
                "/api/import/minio", json={"bucket": "fixture", "key": "demo.parquet"}
            ).json()
            assert r == {"snapshot": sid, "reused": True}
            r = c.post(
                "/api/import/minio",
                json={"bucket": "fixture", "key": "demo.parquet", "refresh": True},
            ).json()
            wait(c, r["job"])
            assert r["snapshot"] != sid
            assert c.get("/api/snapshots/" + sid).json()["status"] == "ready"


def test_bad_dimensions_and_missing_schema(tmp_path):
    store = Store(tmp_path / "data")
    path = generate(tmp_path / "demo.parquet")
    data = pq.read_table(path).to_pylist()
    data[-1]["embedding"] = [1, 2]
    pq.write_table(pa.Table.from_pylist(data), path)
    sid = new_snapshot(store, "bad")
    with pytest.raises(ValueError, match="inconsistent embedding dimensions"):
        ingest(store, sid, path, lambda *_: None)
    pq.write_table(pa.table({"content": ["x"]}), path)
    sid = new_snapshot(store, "missing")
    with pytest.raises(ValueError, match="Missing required columns"):
        ingest(store, sid, path, lambda *_: None)
    store.pool.shutdown()


def test_gpu_matches_cpu_if_available(demo):
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() == 0:
            pytest.skip("No CUDA device")
    except Exception:
        pytest.skip("CUDA unavailable; run on target A100")
    _, sid, store = demo
    engine = Search(store, sid)
    ids = population(store, sid, {"split": "test"})
    cpu, ci, _ = engine.query(ids, {"split": "train"}, "exact_cpu")
    gpu, gi, _ = engine.query(ids, {"split": "train"}, "exact_gpu")
    np.testing.assert_allclose(cpu, gpu, atol=2e-6)


@pytest.mark.parametrize(
    "path,name",
    [
        ("a.py", "python"),
        ("a.tsx", "typescript"),
        ("a.weird", "plaintext"),
        ("a.ps1", "powershell"),
    ],
)
def test_language_inference(path, name):
    assert language(path) == name
