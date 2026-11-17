import io
import json

import pyarrow as pa
import pyarrow.parquet as pq

from test_workflows import demo, run, wait
from zookeeper.demo import generate


def test_conflict_groups_use_cached_pairs_and_keep_pair_reviews(demo):
    c, sid, _ = demo
    base = f"/api/snapshots/{sid}"
    result = run(
        c,
        sid,
        source={"split": "train"},
        reference={"split": "train"},
        opposite=True,
        mode="exact_cpu",
    )
    route = base + f"/audits/{result['key']}/groups"
    jobs = len(c.get("/api/jobs").json())
    response = c.get(route, params={"cutoff": 0.99})
    assert response.status_code == 200, response.text
    groups = response.json()
    group = next(g for g in groups["items"] if g["group_id"] == 7)
    assert group["rows"] == 3 and group["label_count"] == 2 and group["pair_count"] == 2
    detail = c.get(route + "/7", params={"cutoff": 0.99}).json()
    assert {r["row_id"] for r in detail["items"]} == {7, 8, 9}
    assert all(p["label"] != p["match_label"] for p in detail["pairs"])
    c.put(
        base + "/reviews",
        json={
            "a": 7,
            "b": 9,
            "workflow": "label",
            "decision": "suspected mislabel",
            "notes": "one changed line",
        },
    )
    filtered = c.get(
        route, params={"cutoff": 0.99, "review_status": "suspected mislabel"}
    ).json()
    assert filtered["items"][0]["rows"] == 2
    assert c.get(route, params={"cutoff": 1}).status_code == 200
    assert len(c.get("/api/jobs").json()) == jobs
    assert c.get(route + "/99999", params={"cutoff": 0.99}).status_code == 404


def test_text_length_slices_are_distinct_from_supplied_size(demo, tmp_path):
    c, _, _ = demo
    records = pq.read_table(generate(tmp_path / "lengths.parquet")).to_pylist()
    records[1]["size"] = 150000  # Dataset size units need not mean character count.
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(records), buffer)
    imported = c.post(
        "/api/import/upload", files={"file": ("lengths.parquet", buffer.getvalue())}
    ).json()
    wait(c, imported["job"])
    sid = imported["snapshot"]
    base = f"/api/snapshots/{sid}"
    result = run(c, sid, source={"split": "test"}, reference={"split": "train"})
    route = base + f"/audits/{result['key']}/coverage"
    length = c.get(route, params={"field": "text_length"}).json()
    assert len(length["items"]) == 1
    assert (
        length["items"][0]["sample_size"] == 7
        and length["items"][0]["reference_rows"] == 7
    )
    size = c.get(route, params={"field": "size"}).json()
    assert next(s for s in size["items"] if s["slice"] == "100000+")["sample_size"] == 1
    source = c.get(route, params={"field": "source"}).json()
    assert (
        next(s for s in source["items"] if s["slice"] == "novel")["reference_rows"] == 0
    )
    reps = c.get(
        base + f"/audits/{result['key']}/representatives",
        params={"scope": json.dumps({"text_length_min": 0, "text_length_max": 1000})},
    ).json()
    assert any(row["a"] == 1 for row in reps["items"])
