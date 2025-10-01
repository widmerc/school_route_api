from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel, Field
from shapely.geometry import Point, mapping
from shapely.ops import linemerge
import geopandas as gpd

from routing_helpers import build_graph_simple, k_routes_mp  # nutzt deine Helfer
# ^ k_routes_mp / build_graph_simple stammen aus deiner Datei routing_helpers.py

app = FastAPI()

# ------------------------------
# Request schema
# ------------------------------
class RouteRequest(BaseModel):
    start: tuple[float, float] = Field(..., description="(E,N) in LV95 / EPSG:2056")
    end:   tuple[float, float] = Field(..., description="(E,N) in LV95 / EPSG:2056")
    method: str = Field("ml", description="ml | rule-based")

# ------------------------------
# Lade beide Netzwerke (einmalig)
# ------------------------------
# >>> Passen: setze die Pfade auf deine Dateien
PATH_ML = r"D:\Masterarbeit\03_Model\Scripts\3_Classificatiaon\3_Classification\data\network_segments_with_predictions_plus_raster_plus_gt_ML.parquet"
PATH_RB = r"D:\Masterarbeit\03_Model\Scripts\3_Classificatiaon\3_Classification\data\edges_with_safety_variants.parquet"

gdf_ml = gpd.read_parquet(PATH_ML)
if "safety_score" not in gdf_ml.columns:
    raise RuntimeError("ML: Spalte 'safety_score' fehlt")
if "length_m" not in gdf_ml.columns:
    gdf_ml["length_m"] = gdf_ml.geometry.length
gdf_ml = gdf_ml[["geometry", "length_m", "safety_score"]].copy()

gdf_rb = gpd.read_parquet(PATH_RB)
# in deinem Batch-Code: safety_robust -> safety_score
if "safety_score" not in gdf_rb.columns and "safety_robust" in gdf_rb.columns:
    gdf_rb = gdf_rb.rename(columns={"safety_robust": "safety_score"})
if "safety_score" not in gdf_rb.columns:
    raise RuntimeError("Rule-based: Spalte 'safety_score' fehlt")
if "length_m" not in gdf_rb.columns:
    gdf_rb["length_m"] = gdf_rb.geometry.length
gdf_rb = gdf_rb[["geometry", "length_m", "safety_score"]].copy()

# CRS (wir nehmen das vom ML-Netz, beide sollten identisch sein)
network_crs = gdf_ml.crs

# Mapping der Fälle
case_ab = {
    (1.0, 0.0): "Fastest",
    (1.0, 1.0): "Balanced",
    (1.0, 3.0): "Safe",
    (0.0, 1.0): "Safest",
}
alpha_beta_list = list(case_ab.keys())

def _pick_gdf(method: str):
    m = method.lower()
    if m in ("ml", "machine-learning", "machine_learning"):
        return gdf_ml
    if m in ("rule-based", "rule_based", "rb"):
        return gdf_rb
    raise HTTPException(status_code=400, detail="method muss 'ml' oder 'rule-based' sein")

# ------------------------------
# Helper: FeatureCollection für 1 OD & 1 Methode
# ------------------------------
def compute_routes_for_single_od(gdf, home: Point, school: Point):
    features = []
    for (a, b) in alpha_beta_list:
        G_ab = build_graph_simple(gdf, alpha=a, beta=b)  # deine Kostenfunktion & Graphlogik
        paths = k_routes_mp(G_ab, home, school, k=1)     # kürzester Pfad (k=1)
        if not paths:
            continue
        path_ab = paths[0]

        geoms = [e["geom"] for e in path_ab["edges"] if e.get("geom") is not None]
        if not geoms:
            continue
        route_geom = linemerge(geoms)

        feat = {
            "type": "Feature",
            "geometry": mapping(route_geom),
            "properties": {
                "alpha": a,
                "beta": b,
                "case": case_ab[(a, b)],
                "total_length_m": path_ab["total_length_m"],
                "total_cost": path_ab["total_cost"],
                "safety_mean": path_ab["safety_mean_lenweighted"],
                "safety_min": path_ab["safety_min_edge"],
                "worst_edge_fid": path_ab.get("worst_edge_fid"),
            },
        }
        features.append(feat)
    return {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": str(network_crs)}
        },
        "features": features
    }

# ------------------------------
# Endpoint: 4 Fälle für 1 OD & 1 Methode
# ------------------------------
@app.post("/route_cases")
def route_cases(req: RouteRequest):
    gdf = _pick_gdf(req.method)
    start_pt = Point(req.start)
    end_pt   = Point(req.end)
    return compute_routes_for_single_od(gdf, start_pt, end_pt)

# ------------------------------
# Root/info endpoint
# ------------------------------
@app.get("/")
def root():
    return {
        "service": "school_route_api",
        "crs": str(network_crs),
        "datasets": ["ml", "rule-based"],
        "endpoints": ["POST /route_cases"],
    }

# ------------------------------
# Favicon
# ------------------------------
@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)
