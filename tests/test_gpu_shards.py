"""Optional device test: cross the reference-shard boundary and exclude self."""

import numpy as np
import pytest

from zookeeper.search import Search


def test_gpu_reference_shards_match_exact_numpy():
    try:
        import cupy as cp

        if cp.cuda.runtime.getDeviceCount() == 0:
            pytest.skip("No CUDA device")
    except Exception:
        pytest.skip("CUDA unavailable; run on the GPU deployment host")
    rng = np.random.default_rng(17)
    vectors = rng.normal(size=(70003, 8)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors[70000] = vectors[17]
    vectors[70001] = vectors[69999]
    vectors[70002] = vectors[65536]
    references = np.arange(70000, dtype=np.int64)
    sources = np.array([0, 70000, 70001, 70002], dtype=np.int64)
    # Isolate the same production GPU routine from storage/index construction.
    engine = Search.__new__(Search)
    engine.vectors = vectors
    actual, ids, mode = engine.gpu_query(sources, references, 3, lambda *_: None)
    expected = vectors[sources] @ vectors[references].T
    expected[0, 0] = -np.inf
    expected_ids = np.argsort(-expected, axis=1, kind="stable")[:, :3]
    np.testing.assert_array_equal(ids, expected_ids)
    np.testing.assert_allclose(
        actual, np.take_along_axis(expected, expected_ids, axis=1), atol=2e-6
    )
    assert mode == "exact_gpu"
    assert ids[1, 0] == 17 and ids[2, 0] == 69999 and ids[3, 0] == 65536
