import itertools as it
import numpy as np
import networkx as nx
import geopandas as gpd

from shapely.geometry import LineString, Point
from shapely.ops import linemerge
from scipy.spatial import cKDTree

# -------------------------------------------------------
# Helpers: Endpunkte & Kosten
# -------------------------------------------------------
def _endpoints(line: LineString):
    """Runde Koordinaten der Linienendpunkte, damit sie stabile Node-Keys sind."""
    a, b = line.coords[0], line.coords[-1]
    return (round(a[0], 6), round(a[1], 6)), (round(b[0], 6), round(b[1], 6))

def _edge_cost(length, safety, alpha=1.0, beta=1.0):
    """
    Kostenfunktion:
    cost = alpha * length + beta * (100 - safety) * (length/100)
    Je höher safety, desto günstiger.
    """
    length = float(length)
    safety = float(safety)
    return alpha * length + beta * (100.0 - safety) * (length / 100.0)

# -------------------------------------------------------
# Graph aufbauen
# -------------------------------------------------------
def build_graph_simple(gdf, alpha=1.0, beta=1.0):
    """
    Baue einen ungerichteten Graphen:
    - Nodes = Linienendpunkte
    - Edges = Liniensegmente
    - Parallelen werden auf das beste Segment reduziert
    """
    G = nx.Graph()
    for _, row in gdf.iterrows():
        geom = row.geometry
        if not isinstance(geom, LineString) or geom.is_empty:
            continue

        u, v = _endpoints(geom)
        length = float(row["length_m"])
        safety = float(row["safety_score"])
        cost   = _edge_cost(length, safety, alpha, beta)

        if u not in G:
            G.add_node(u, x=u[0], y=u[1])
        if v not in G:
            G.add_node(v, x=v[0], y=v[1])

        attrs = dict(
            fid=row.get("fid"),
            length_m=length,
            safety_score=safety,
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

    return G

# -------------------------------------------------------
# NodeLocator: Snap auf nächste Node
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
# Edge-Map & Pfad-Zusammenfassung
# -------------------------------------------------------
def _make_edge_map(G):
    """Leichtgewichtige Kanten-Dict (für Summaries)."""
    def key(u, v): return (u, v) if u <= v else (v, u)
    return {key(u, v): dict(data) for u, v, data in G.edges(data=True)}

def _summarize_path_from_map(path, emap):
    """Summarisiere einen Pfad (Länge, Kosten, Safety etc.)."""
    def key(u, v): return (u, v) if u <= v else (v, u)

    total_len, total_cost, min_safety = 0.0, 0.0, float("inf")
    worst_edge = None
    edges = []

    for u, v in zip(path[:-1], path[1:]):
        d = emap[key(u, v)]
        edges.append(d)
        total_len  += d["length_m"]
        total_cost += d["cost"]
        if d["safety_score"] < min_safety:
            min_safety = d["safety_score"]
            worst_edge = d.get("fid")

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
# GeoDataFrame-Helfer
# -------------------------------------------------------
def make_routes_gdf(paths, crs, extra=None):
    """Wandle berechnete Pfade in ein GeoDataFrame um."""
    feats = []
    for i, p in enumerate(paths, 1):
        geoms = [e["geom"] for e in p["edges"] if e.get("geom") is not None]
        if not geoms:
            continue
        route_geom = linemerge(geoms)
        rec = {
            "alt": i,
            "total_length_m": p["total_length_m"],
            "total_cost": p["total_cost"],
            "safety_mean": p["safety_mean_lenweighted"],
            "safety_min": p["safety_min_edge"],
            "worst_edge_fid": p.get("worst_edge_fid"),
            "geometry": route_geom
        }
        if extra:
            rec.update(extra)
        feats.append(rec)
    return gpd.GeoDataFrame(feats, geometry="geometry", crs=crs)

# -------------------------------------------------------
# K-Routen (Shortest Alternatives)
# -------------------------------------------------------
def k_routes_mp(G, src_pt: Point, dst_pt: Point, k=1):
    """
    Berechne k kürzeste Pfade zwischen zwei Punkten.
    Nutzt NetworkX shortest_simple_paths.
    """
    locator = NodeLocator(G)
    s, t = locator.nearest(src_pt), locator.nearest(dst_pt)

    gen = nx.shortest_simple_paths(G, s, t, weight="cost")
    paths = list(it.islice(gen, k))
    emap = _make_edge_map(G)

    return [_summarize_path_from_map(p, emap) for p in paths]
