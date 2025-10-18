# ==========================================================
# School Route Routing API (Optimized + Cached + Single/Multiple Cases)
# ==========================================================
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from shapely.geometry import Point, mapping
from shapely.ops import linemerge
import geopandas as gpd
import networkx as nx
from routing_helpers import build_graph_simple, NodeLocator, _edge_cost, _make_edge_map, _summarize_path_from_map

app = FastAPI(title="School Route API (Cached + Flexible αβ)")

# ----------------------------------------------------------
# Request model
# ----------------------------------------------------------
class RouteRequest(BaseModel):
    start: tuple[float, float] = Field(..., description="Start point (E, N) in LV95 / EPSG:2056")
    end: tuple[float, float] = Field(..., description="End point (E, N) in LV95 / EPSG:2056")
    model: str = Field("ml", description="'ml' or 'rule-based'")
    alpha: float | None = Field(None, description="Optional alpha parameter")
    beta: float | None = Field(None, description="Optional beta parameter")

# ----------------------------------------------------------
# Globals (filled on startup)
# ----------------------------------------------------------
PARQUET_PATH = r"./data/routenetwork.parquet"
gdf_all = None
G_ml = None
G_rb = None
locator_ml = None
locator_rb = None
emap_ml = None
emap_rb = None
network_crs = None

# Default (alpha, beta) combinations
case_ab = {
    (1.0, 0.0): "Fastest",
    (1.0, 1.0): "Balanced",
    (1.0, 3.0): "Safe",
    (0.0, 1.0): "Safest",
}

# ----------------------------------------------------------
# Helper functions
# ----------------------------------------------------------
def update_edge_costs(G: nx.Graph, alpha: float, beta: float):
    """Update edge cost weights efficiently."""
    for _, _, data in G.edges(data=True):
        data["cost"] = _edge_cost(data["length_m"], data["safety_score"], alpha, beta)

def shortest_route(G: nx.Graph, locator: NodeLocator, emap: dict, src_pt: Point, dst_pt: Point):
    """Compute shortest path (k=1)."""
    s, t = locator.nearest(src_pt), locator.nearest(dst_pt)
    path = next(nx.shortest_simple_paths(G, s, t, weight="cost"))
    return _summarize_path_from_map(path, emap)

def route_to_feature(route_summary: dict, case_label: str, alpha: float, beta: float):
    """Convert route summary to GeoJSON Feature."""
    geoms = [e["geom"] for e in route_summary["edges"] if e.get("geom") is not None]
    if not geoms:
        return None
    merged = linemerge(geoms)
    return {
        "type": "Feature",
        "geometry": mapping(merged),
        "properties": {
            "case": case_label,
            "alpha": alpha,
            "beta": beta,
            "total_length_m": route_summary["total_length_m"],
            "total_cost": route_summary["total_cost"],
            "safety_mean": route_summary["safety_mean_lenweighted"],
            "safety_min": route_summary["safety_min_edge"],
            "worst_edge_fid": route_summary["worst_edge_fid"],
        },
    }

# ----------------------------------------------------------
# Startup: preload and cache networks
# ----------------------------------------------------------
@app.on_event("startup")
def load_networks():
    global gdf_all, G_ml, G_rb, locator_ml, locator_rb, emap_ml, emap_rb, network_crs

    print(f"🔹 Loading Parquet: {PARQUET_PATH}")
    gdf_all = gpd.read_parquet(PARQUET_PATH)
    if not {"safety_score_ML", "safety_score_RB"}.issubset(gdf_all.columns):
        raise RuntimeError("Parquet must contain 'safety_score_ML' and 'safety_score_RB'.")

    if "length_m" not in gdf_all.columns:
        gdf_all["length_m"] = gdf_all.geometry.length

    network_crs = gdf_all.crs

    # ML network
    gdf_ml = gdf_all[["geometry", "length_m", "safety_score_ML"]].rename(columns={"safety_score_ML": "safety_score"})
    G_ml = build_graph_simple(gdf_ml, alpha=1.0, beta=1.0)
    locator_ml = NodeLocator(G_ml)
    emap_ml = _make_edge_map(G_ml)

    # Rule-based network
    gdf_rb = gdf_all[["geometry", "length_m", "safety_score_RB"]].rename(columns={"safety_score_RB": "safety_score"})
    G_rb = build_graph_simple(gdf_rb, alpha=1.0, beta=1.0)
    locator_rb = NodeLocator(G_rb)
    emap_rb = _make_edge_map(G_rb)

    print("✅ Networks preloaded and cached.")

# ----------------------------------------------------------
# Main endpoint
# ----------------------------------------------------------
@app.post("/route_cases")
def route_cases(req: RouteRequest):
    model = req.model.lower()
    if model not in ["ml", "rule-based", "rule_based"]:
        raise HTTPException(status_code=400, detail="Model must be 'ml' or 'rule-based'.")

    if model.startswith("ml"):
        G, locator, emap = G_ml, locator_ml, emap_ml
    else:
        G, locator, emap = G_rb, locator_rb, emap_rb

    start_pt, end_pt = Point(req.start), Point(req.end)

    # Case 1: Custom α/β provided
    if req.alpha is not None and req.beta is not None:
        update_edge_costs(G, req.alpha, req.beta)
        summary = shortest_route(G, locator, emap, start_pt, end_pt)
        feat = route_to_feature(summary, f"Custom α={req.alpha}, β={req.beta}", req.alpha, req.beta)
        return {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": str(network_crs)}},
            "features": [feat] if feat else []
        }

    # Case 2: Default 4 routes
    features = []
    for (a, b), label in case_ab.items():
        update_edge_costs(G, a, b)
        summary = shortest_route(G, locator, emap, start_pt, end_pt)
        feat = route_to_feature(summary, label, a, b)
        if feat:
            features.append(feat)

    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": str(network_crs)}},
        "features": features,
    }

@app.get("/")
def root():
    return {
        "service": "school_route_api_cached",
        "description": "Compute 1 or 4 school routes depending on α/β presence",
        "expected_fields": ["start", "end", "model", "alpha", "beta"],
        "models": ["ml", "rule-based"],
        "endpoint": "/route_cases",
    }
