import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from zookeeper.ingest import ingest, new_snapshot
from zookeeper.search import Search
from zookeeper.storage import Store


def test_persistent_approximate_index_reranks_and_filters_reference(tmp_path):
    rng = np.random.default_rng(19)
    vectors = rng.normal(size=(1201, 8)).astype(np.float32)
    vectors[-1] = vectors[4]
    records = [
        {
            "content": "def example(): return 1\n",
            "path": f"{i}.py",
            "split": "test" if i == 1200 else "train",
            "label": i % 2,
            "embedding": v.tolist(),
            "size": 24,
            "hash": None,
            "score": i,
            "source": "sparse" if i in [4, 8, 12] else "other",
        }
        for i, v in enumerate(vectors)
    ]
    path = tmp_path / "fixture.parquet"
    pq.write_table(pa.Table.from_pylist(records), path)
    store = Store(tmp_path / "data")
    sid = new_snapshot(store, "approximate fixture")
    ingest(store, sid, path, lambda *_: None)
    search = Search(store, sid)
    scores, ids, mode = search.query(
        np.array([1200]), {"split": "train"}, "approximate", nprobe=4096
    )
    assert mode == "approximate_reranked" and ids[0, 0] == 4
    assert abs(scores[0, 0] - 1) < 2e-6
    assert list((store.path(sid) / "indexes").glob("*.faiss"))
    scores2, ids2, _ = Search(store, sid).query(
        np.array([1200]), {"split": "train"}, "approximate", nprobe=4096
    )
    np.testing.assert_array_equal(ids, ids2)
    np.testing.assert_array_equal(scores, scores2)
    _, ids, _ = search.query(np.array([1200]), {"source": "sparse", "label": 0})
    assert ids[0, 0] == 4
    _, ids, _ = search.query(np.array([4]), {"source": "sparse", "label": 0})
    assert ids[0, 0] in [8, 12]
    from zookeeper.search import population

    assert population(store, sid, {"extra:score": "4"}).tolist() == [4]
    with store.catalog(sid) as c:
        assert c.execute("SELECT label FROM rows WHERE row_id=4").fetchone()[0] == 0
    store.pool.shutdown()
