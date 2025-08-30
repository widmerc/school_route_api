school_route_api

FastAPI service that computes k alternative routes on a projected road network, taking length and safety scores into account.


Overview

school_route_api builds a simple graph from a GeoParquet road network (`data/edges_with_safety.parquet`) and returns k alternative routes between two points as a GeoJSON FeatureCollection. Each route feature includes metrics such as total length (meters), length-weighted safety, and the worst-edge id.

Features
- Compute k alternative routes using a cost function that combines length and a safety score
- Returns GeoJSON FeatureCollection with per-route properties (length, safety metrics, worst edge)
- Designed to run as a small FastAPI service; bundleable with Docker for deployment

Quickstart (local)

Requirements
- Python 3.10+
- See `requirements.txt`

Run locally
```powershell
python -m venv .venv
. .\.venv\Scripts\Activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Example request (POST /route)
- JSON body:
```json
{ "start": [2682936.3, 1248361.9], "end": [2683800.8, 1247199.3], "k": 3 }
```
- curl (PowerShell):
```powershell
curl.exe -X POST "http://127.0.0.1:8000/route" -H "Content-Type: application/json" -d '{"start":[2682936.3,1248361.9],"end":[2683800.8,1247199.3],"k":3}'
```

Response
- GeoJSON FeatureCollection. Each feature contains a `LineString` geometry (projected coordinates) and `properties` including `alt`, `total_length_m`, `safety_mean`, `safety_min`, and `worst_edge_fid`.

Notes
- CRS: Input and output coordinates are projected (meters). QGIS may interpret GeoJSON as EPSG:4326 by default; set the layer CRS to the same CRS as `edges_with_safety.parquet` when loading the result, or transform geometries to EPSG:4326 in the API if you prefer lat/lon.
- Data file: `data/edges_with_safety.parquet` (~1.46 MB) can be kept in the repo for small deployments. For larger datasets, consider external object storage (S3) or a persistent disk.
- Deployment: Recommended on Docker-friendly hosts (Render, Fly, Railway, VPS). Vercel is not ideal for heavy geospatial native dependencies; use Vercel only as a frontend/proxy if needed.

Contact
- For deployment help or modifications (e.g., transform output to EPSG:4326), open an issue or request a patch.



# Deploy FastAPI on Render

Use this repo as a template to deploy a Python [FastAPI](https://fastapi.tiangolo.com) service on Render.

See https://render.com/docs/deploy-fastapi or follow the steps below:

## Manual Steps

1. You may use this repository directly or [create your own repository from this template](https://github.com/render-examples/fastapi/generate) if you'd like to customize the code.
2. Create a new Web Service on Render.
3. Specify the URL to your new repository or this repository.
4. Render will automatically detect that you are deploying a Python service and use `pip` to download the dependencies.
5. Specify the following as the Start Command.

    ```shell
    uvicorn main:app --host 0.0.0.0 --port $PORT
    ```

6. Click Create Web Service.

Or simply click:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/render-examples/fastapi)

## Thanks

Thanks to [Harish](https://harishgarg.com) for the [inspiration to create a FastAPI quickstart for Render](https://twitter.com/harishkgarg/status/1435084018677010434) and for some sample code!