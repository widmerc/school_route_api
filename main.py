from fastapi import FastAPI
from pydantic import BaseModel
from shapely.geometry import Point, mapping
from shapely.ops import linemerge
import geopandas as gpd

# Import deine Routing-Funktionen
from routing_helpers import build_graph_simple, k_routes_mp

app = FastAPI()

# ------------------------------
# Request schema
# ------------------------------
class RouteRequest(BaseModel):
    start: tuple[float, float]
    end: tuple[float, float]
    alpha: float = 1.0
    beta: float = 1.0
    k: int = 3

# ------------------------------
# Load your network once
# ------------------------------
gdf = gpd.read_parquet("data/edges_with_safety.parquet")
G = build_graph_simple(gdf, alpha=1.0, beta=1.0)

# ------------------------------
# Endpoint
# ------------------------------
@app.post("/route")
def compute_route(req: RouteRequest):
    start_pt = Point(req.start)
    end_pt   = Point(req.end)

    alts = k_routes_mp(G, start_pt, end_pt, k=req.k, n_jobs=1, backend="threading")

    features = []
    for i, p in enumerate(alts, 1):
        geoms = [e["geom"] for e in p["edges"] if e.get("geom")]
        if not geoms:
            continue
        route_geom = linemerge(geoms)
        features.append({
            "type": "Feature",
            "geometry": mapping(route_geom),
            "properties": {
                "alt": i,
                "total_length_m": p["total_length_m"],
                "safety_mean": p["safety_mean_lenweighted"],
                "safety_min": p["safety_min_edge"],
                "worst_edge_fid": p["worst_edge_fid"],
            }
        })
    return {"type": "FeatureCollection", "features": features}
