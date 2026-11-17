"""Connected review groups from cached opposite-label neighbour links."""

import numpy as np
import pyarrow as pa
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def conflict_membership(edges):
    schema = pa.schema([("row_id", pa.int64()), ("group_id", pa.int64())])
    if not len(edges):
        return pa.Table.from_pylist([], schema=schema)
    a, b = edges["a"].to_numpy(), edges["b"].to_numpy()
    nodes, inverse = np.unique(np.concatenate([a, b]), return_inverse=True)
    graph = coo_matrix(
        (np.ones(len(a), dtype=np.int8), (inverse[: len(a)], inverse[len(a) :])),
        shape=(len(nodes), len(nodes)),
    ).tocsr()
    count, labels = connected_components(graph, directed=False)
    representatives = np.full(count, np.iinfo(np.int64).max, dtype=np.int64)
    np.minimum.at(representatives, labels, nodes)
    return pa.table(
        {"row_id": nodes, "group_id": representatives[labels]}, schema=schema
    )
