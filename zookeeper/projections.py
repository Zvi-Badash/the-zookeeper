"""Cached navigation projections. All audit scoring stays in the original space."""

import numpy as np
from sklearn.decomposition import IncrementalPCA, PCA
from sklearn.manifold import TSNE

from .search import population


def project(store, sid, method, progress=lambda p, m: None):
    if method not in {"pca", "umap", "tsne"}:
        raise ValueError("Unknown projection")
    path = store.path(sid) / f"{method}.npz"
    if path.exists():
        return {"method": method, "cached": True}
    ids = population(store, sid, {})
    v = store.vectors(sid)
    limits = {"pca": len(ids), "umap": 15000, "tsne": 5000}
    if len(ids) > limits[method]:
        ids = np.sort(
            np.random.default_rng(42).choice(ids, limits[method], replace=False)
        )
    if len(ids) < 4:
        xyz = np.zeros((len(ids), 3), np.float32)
        if len(ids):
            coords = PCA(n_components=min(3, len(ids), v.shape[1])).fit_transform(
                v[ids]
            )
            xyz[:, : coords.shape[1]] = coords
    elif method == "pca":
        nc = min(3, v.shape[1])
        model = IncrementalPCA(n_components=nc, batch_size=2048)
        # array_split avoids a final batch smaller than the component count.
        chunks = np.array_split(ids, max(1, len(ids) // 2048))
        for j, chunk in enumerate(chunks):
            model.partial_fit(v[chunk])
            progress(0.5 * (j + 1) / len(chunks), "Fitting PCA")
        xyz = np.zeros((len(ids), 3), np.float32)
        start = 0
        for j, chunk in enumerate(chunks):
            xyz[start : start + len(chunk), :nc] = model.transform(v[chunk])
            start += len(chunk)
            progress(0.5 + 0.5 * (j + 1) / len(chunks), "Projecting PCA")
    else:
        # Reduce sampled high-dimensional data before costly navigation-only fits.
        progress(
            0.05, f"Reducing sample for {method}; fit progress is reported by phase"
        )
        values = PCA(
            n_components=min(50, len(ids) - 1, v.shape[1]), random_state=42
        ).fit_transform(v[ids])
        progress(0.2, f"Fitting {method} on {len(ids):,} sampled rows")
        if method == "umap":
            from umap import UMAP

            xyz = UMAP(
                n_components=3,
                n_neighbors=min(15, len(ids) - 1),
                random_state=42,
                n_jobs=1,
                init="random",
            ).fit_transform(values)
        else:
            if values.shape[1] < 3:
                values = np.pad(values, ((0, 0), (0, 3 - values.shape[1])))
            xyz = TSNE(
                n_components=3,
                method="barnes_hut",
                perplexity=min(30, max(2, (len(ids) - 1) / 3)),
                random_state=42,
                init="pca",
                learning_rate="auto",
            ).fit_transform(values)
    temporary = path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, ids=ids, xyz=xyz)
    temporary.replace(path)
    return {"method": method, "represented": len(ids)}


def cohort_distances(store, sid, left, right, sample=10000):
    a, b = population(store, sid, left), population(store, sid, right)
    if not len(a) or not len(b):
        raise ValueError("Both cohorts need usable embeddings")
    rng = np.random.default_rng(42)
    ai, bi = rng.choice(a, sample), rng.choice(b, sample)
    vectors = store.vectors(sid)
    distances = np.empty(sample, np.float32)
    for start in range(0, sample, 256):
        aa, bb = ai[start : start + 256], bi[start : start + 256]
        distances[start : start + len(aa)] = 1 - np.clip(
            np.einsum("ij,ij->i", vectors[aa], vectors[bb]), -1, 1
        )
    hist, edges = np.histogram(distances, bins=40, range=(0, 2))
    return {
        "kind": "random-pair cosine distance (1 − cosine similarity)",
        "population_a": len(a),
        "population_b": len(b),
        "sample_pairs": sample,
        "self_pairs": int((ai == bi).sum()),
        "mean": float(distances.mean()),
        "median": float(np.median(distances)),
        "quantiles": np.quantile(distances, [0.1, 0.25, 0.75, 0.9]).tolist(),
        "counts": hist.tolist(),
        "edges": edges.tolist(),
    }
