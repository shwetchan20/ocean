from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from contouring import extract_contours
from data_adapters import build_adapter, demo_observations, load_observations_csv

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"

app = FastAPI(title="OceanScope 3D API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

ADAPTER = build_adapter(DATA)
META = ADAPTER.metadata()
OBS = load_observations_csv(DATA / "observations.csv", demo_observations(META.times))
VARIABLE_IDS = {v["id"] for v in META.variables}


@lru_cache(maxsize=64)
def _cached_field(time_idx: int, variable: str) -> tuple[bytes, tuple[int, int, int]]:
    """Cache raw field arrays (as bytes) keyed by (time, variable) so scrubbing
    the depth slider — which re-requests the same time/variable — doesn't
    recompute the whole 3D field every time."""
    arr = np.asarray(ADAPTER.field(time_idx, variable), dtype=float)
    return arr.tobytes(), arr.shape


def get_field(time_idx: int, variable: str) -> np.ndarray:
    if variable not in VARIABLE_IDS:
        raise HTTPException(400, f"Unsupported variable: {variable}. Available: {sorted(VARIABLE_IDS)}")
    raw, shape = _cached_field(time_idx, variable)
    return np.frombuffer(raw, dtype=float).reshape(shape)


def nearest_index(values: np.ndarray, target: float) -> int:
    return int(np.argmin(np.abs(values - target)))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "dataset": META.source, "netcdf_active": META.source != "demo"}


@app.get("/api/metadata")
def metadata() -> dict[str, Any]:
    return {
        "variables": META.variables,
        "times": META.times,
        "depths": META.depths.tolist(),
        "lat_range": [float(META.lats.min()), float(META.lats.max())],
        "lon_range": [float(META.lons.min()), float(META.lons.max())],
        "source": META.source,
        "palettes": ["thermal", "viridis-ish", "cool-warm"],
    }


@app.get("/api/ocean")
def ocean(time: int = Query(0, ge=0), variable: str = "temperature") -> dict[str, Any]:
    ti = max(0, min(time, len(META.times) - 1))
    values = get_field(ti, variable)
    flat = values.ravel()
    return {
        "time": META.times[ti], "time_index": ti, "variable": variable,
        "shape": list(values.shape),
        "lat": META.lats.tolist(), "lon": META.lons.tolist(), "depth": META.depths.tolist(),
        "values": np.round(flat, 4).tolist(),
        "min": float(np.nanmin(values)), "max": float(np.nanmax(values)),
    }


@app.get("/api/currents")
def currents(time: int = Query(0, ge=0), depth_index: int = Query(0, ge=0), stride: int = Query(2, ge=1, le=6)) -> dict[str, Any]:
    """Downsampled vector grid (lat, lon, u, v) at one depth layer, for arrow-field rendering."""
    if "current_u" not in VARIABLE_IDS or "current_v" not in VARIABLE_IDS:
        raise HTTPException(404, "Current vector components not available in this dataset")
    ti = max(0, min(time, len(META.times) - 1))
    di = max(0, min(depth_index, len(META.depths) - 1))
    u = get_field(ti, "current_u")[:, :, di]
    v = get_field(ti, "current_v")[:, :, di]
    vectors = []
    for i in range(0, len(META.lats), stride):
        for j in range(0, len(META.lons), stride):
            vectors.append({
                "lat": float(META.lats[i]), "lon": float(META.lons[j]),
                "u": round(float(u[i, j]), 4), "v": round(float(v[i, j]), 4),
            })
    return {"time": META.times[ti], "depth": float(META.depths[di]), "vectors": vectors}


@app.get("/api/isosurface")
def isosurface(time: int = Query(0, ge=0), variable: str = "temperature", threshold: float | None = None) -> dict[str, Any]:
    """Marching-squares isoline extraction per depth layer, stacked into a
    pseudo-3D isosurface. See contouring.py for the algorithm."""
    ti = max(0, min(time, len(META.times) - 1))
    values = get_field(ti, variable)
    if threshold is None:
        # Fields like temperature/chlorophyll have far more horizontal
        # spread near the surface than at depth, so anchoring the default
        # threshold to the global 3D mean tends to sit outside most deep
        # layers' range and produce empty contours everywhere. The surface
        # layer's median is a better default: it guarantees a threshold
        # that actually bisects the layer with the most structure.
        threshold = float(np.nanmedian(values[:, :, 0]))
    layers = []
    for di, d in enumerate(META.depths):
        slice2d = values[:, :, di]
        polylines = extract_contours(slice2d, META.lons, META.lats, threshold)
        layers.append({"depth": float(d), "polylines": [[[p[0], p[1]] for p in line] for line in polylines]})
    return {"time": META.times[ti], "variable": variable, "threshold": threshold, "layers": layers}


@app.get("/api/observations")
def observations(type: str | None = None) -> list[dict[str, Any]]:
    return [
        {"id": o.id, "type": o.type, "lat": o.lat, "lon": o.lon, "timestamp": o.timestamp}
        for o in OBS if not type or o.type.lower() == type.lower()
    ]


@app.get("/api/observations/{obs_id}/profile")
def profile(obs_id: str, time: int = Query(0, ge=0)) -> dict[str, Any]:
    for o in OBS:
        if o.id == obs_id:
            ti = max(0, min(time, len(META.times) - 1))
            lat_i = nearest_index(META.lats, o.lat)
            lon_i = nearest_index(META.lons, o.lon)
            model_temp, model_sal = [], []
            if "temperature" in VARIABLE_IDS:
                t_field = get_field(ti, "temperature")
                for d in o.depths:
                    di = nearest_index(META.depths, d)
                    model_temp.append(round(float(t_field[lat_i, lon_i, di]), 2))
            if "salinity" in VARIABLE_IDS:
                s_field = get_field(ti, "salinity")
                for d in o.depths:
                    di = nearest_index(META.depths, d)
                    model_sal.append(round(float(s_field[lat_i, lon_i, di]), 2))
            return {
                "id": o.id, "type": o.type, "lat": o.lat, "lon": o.lon, "timestamp": o.timestamp,
                "depths": o.depths,
                "observed": {"temperature": o.temperature, "salinity": o.salinity},
                "model": {"temperature": model_temp, "salinity": model_sal},
            }
    raise HTTPException(404, "Observation not found")


@app.get("/api/observations/{obs_id}/track")
def track(obs_id: str) -> dict[str, Any]:
    for o in OBS:
        if o.id == obs_id:
            return {"id": o.id, "type": o.type, "track": o.track}
    raise HTTPException(404, "Observation not found")


app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")
