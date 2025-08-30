import os, itertools as it
import numpy as np
import networkx as nx
import geopandas as gpd

from shapely.geometry import LineString, Point
from shapely.ops import linemerge
from scipy.spatial import cKDTree
from tqdm.auto import tqdm
from joblib import Parallel, delayed

# -------------------------------------------------------
# Helpers: endpoints & edge cost
# -------------------------------------------------------
def _endpoints(line: LineString):
    """Return (x,y) start/end tuples, rounded for stable node keys."""
    a, b = line.coords[0], line.coords[-1]
    return (round(a[0], 6), round(a[1], 6)), (round(b[0], 6), round(b[1], 6))

def _edge_cost(length, safety, alpha=1.0, beta=1.0):
    """
    cost_e = alpha * length_m + beta * (100 - S_e) * (length_m / 100)
    S_e in [0,100], higher is safer.
    """
    length = float(length); safety = float(safety)
    return alpha * length + beta * (100.0 - safety) * (length / 100.0)

# -------------------------------------------------------
# Build Graph (collapse parallels)
# -------------------------------------------------------
def build_graph_simple(gdf, alpha=1.0, beta=1.0):
    """
    Build a simple undirected graph:
    - nodes = segment endpoints
    - edges = road segments
    - collapse parallels by min cost (tie -> lower safety)
    """
    G = nx.Graph()
    need = [c for c in ("length_m", "safety_score") if c not in gdf.columns]
    if need:
        raise ValueError(f"Missing columns for routing: {need}")

    for _, row in gdf.iterrows():
        geom = row.geometry
        if not isinstance(geom, LineString) or geom.is_empty:
            continue

        u, v = _endpoints(geom)
        length = float(row["length_m"])
        safety = float(row["safety_score"])
        cost   = _edge_cost(length, safety, alpha, beta)

        if u not in G: G.add_node(u, x=u[0], y=u[1])
        if v not in G: G.add_node(v, x=v[0], y=v[1])

        attrs = dict(
            fid=row.get("fid"),
            length_m=length,
            safety_score=safety,
            prob_unsafe=float(row.get("prob_unsafe", np.nan)),
            cost=cost,
            geom=geom
        )

        if G.has_edge(u, v):
            curr = G[u][v]
            better = (cost < curr["cost"]) or (
                np.isclose(cost, curr["cost"]) and safety < curr["safety_score"]
            )
            if better:
                G[u][v].update(attrs)
        else:
            G.add_edge(u, v, **attrs)

    print(f"[Graph built] {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G

# -------------------------------------------------------
# NodeLocator (snap point to nearest node)
# -------------------------------------------------------
class NodeLocator:
    def __init__(self, G):
        coords = np.array([(n[0], n[1]) for n in G.nodes()])
        self.nodes = list(G.nodes())
        self.tree = cKDTree(coords)

    def nearest(self, pt: Point):
        _, idx = self.tree.query([pt.x, pt.y])
        return self.nodes[int(idx)]

# -------------------------------------------------------
# Compact edge map for multiprocessing
# -------------------------------------------------------
def _make_edge_map(G):
    """Create lightweight dict so workers don’t need the whole Graph."""
    def key(u, v): return (u, v) if u <= v else (v, u)
    emap = {}
    for u, v, data in G.edges(data=True):
        emap[key(u, v)] = {
            "length_m": data["length_m"],
            "cost": data["cost"],
            "safety_score": data["safety_score"],
            "fid": data.get("fid"),
            "geom": data.get("geom"),
        }
    return emap

# -------------------------------------------------------
# Summarize one path
# -------------------------------------------------------
def _summarize_path_from_map(path, emap):
    def key(u, v): return (u, v) if u <= v else (v, u)
    total_len = 0.0
    total_cost = 0.0
    min_safety = float("inf")
    worst_edge = None
    edges = []

    for u, v in zip(path[:-1], path[1:]):
        d = emap[key(u, v)]
        edges.append(d)
        total_len  += d["length_m"]
        total_cost += d["cost"]
        if d["safety_score"] < min_safety:
            min_safety = d["safety_score"]; worst_edge = d.get("fid")

    lw_mean = (sum(e["safety_score"] * e["length_m"] for e in edges) /
               max(total_len, 1e-9))

    return dict(
        nodes=path,
        edges=edges,
        total_length_m=total_len,
        total_cost=total_cost,
        safety_mean_lenweighted=lw_mean,
        safety_min_edge=min_safety,
        worst_edge_fid=worst_edge
    )

# -------------------------------------------------------
# k shortest alternatives (with tqdm + parallel summary)
# -------------------------------------------------------
def k_routes_mp(G, src_pt: Point, dst_pt: Point,
                k=10, n_jobs=1, backend="threading", show_progress=True):
    """
    Compute k shortest paths between two points.
    Uses NetworkX shortest_simple_paths (sequential) + parallel summary.
    """
    locator = NodeLocator(G)
    s, t = locator.nearest(src_pt), locator.nearest(dst_pt)

    # 1) Collect paths
    gen = nx.shortest_simple_paths(G, s, t, weight="cost")
    paths = ([p for p in tqdm(it.islice(gen, k), total=k, desc="Collect paths")]
             if show_progress else list(it.islice(gen, k)))

    # 2) Summarize in parallel
    emap = _make_edge_map(G)
    iterator = (delayed(_summarize_path_from_map)(p, emap) for p in paths)
    if show_progress:
        iterator = tqdm(iterator, total=len(paths), desc="Summarize paths")

    summaries = Parallel(n_jobs=n_jobs, backend=backend)(iterator)
    return summaries
