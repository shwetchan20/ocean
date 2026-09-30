"""Data ingestion adapters.

The PS explicitly asks for "a modular architecture that allows new variables
or data sources to be added with minimal code change". This module is that
seam: every adapter exposes the same small interface

    class SomeAdapter:
        def metadata(self) -> GridMeta: ...
        def field(self, time_idx: int, variable: str) -> np.ndarray:  # shape (nlat, nlon, ndepth)

so `main.py` never needs to know whether the data came from a real NetCDF
file, a CSV, or the synthetic demo generator. Add a new adapter class here
and register it in `build_adapter()` to plug in a new source.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import numpy as np

# CF-convention-ish variable name aliases we try when reading a real NetCDF
# file, since different producers (INCOIS LAS, Copernicus Marine) name the
# same physical quantity differently.
VARIABLE_ALIASES: dict[str, list[str]] = {
    "temperature": ["temperature", "thetao", "temp", "water_temp", "sea_water_temperature"],
    "salinity": ["salinity", "so", "sal", "sea_water_salinity"],
    "chlorophyll": ["chlorophyll", "chl", "chlor_a", "CHL"],
    "current_u": ["current_u", "uo", "u", "eastward_sea_water_velocity"],
    "current_v": ["current_v", "vo", "v", "northward_sea_water_velocity"],
}
DIM_ALIASES: dict[str, list[str]] = {
    "lat": ["lat", "latitude", "y"],
    "lon": ["lon", "longitude", "x"],
    "depth": ["depth", "lev", "deptht", "z"],
    "time": ["time", "time_counter", "t"],
}


@dataclass
class GridMeta:
    lats: np.ndarray
    lons: np.ndarray
    depths: np.ndarray
    times: list[str]
    variables: list[dict[str, str]]
    source: str


class DemoAdapter:
    """Deterministic synthetic dataset so the project runs with zero external
    downloads or credentials. Structured like a real 4D ocean cube
    (lat x lon x depth x time) so swapping in real data changes nothing
    downstream."""

    def __init__(self) -> None:
        self.lats = np.linspace(5.0, 23.0, 24)
        self.lons = np.linspace(68.0, 88.0, 28)
        self.depths = np.array([0, 25, 50, 100, 200, 400, 700, 1200, 2000], dtype=float)
        self.times = [
            "2026-01-01T00:00:00Z", "2026-01-01T06:00:00Z", "2026-01-01T12:00:00Z",
            "2026-01-01T18:00:00Z", "2026-01-02T00:00:00Z", "2026-01-02T06:00:00Z",
            "2026-01-02T12:00:00Z", "2026-01-02T18:00:00Z",
        ]

    def metadata(self) -> GridMeta:
        return GridMeta(
            lats=self.lats, lons=self.lons, depths=self.depths, times=self.times,
            variables=[
                {"id": "temperature", "label": "Sea Surface / Subsurface Temperature", "unit": "°C"},
                {"id": "salinity", "label": "Salinity", "unit": "PSU"},
                {"id": "chlorophyll", "label": "Chlorophyll-a", "unit": "mg/m³"},
                {"id": "current_u", "label": "Current (eastward)", "unit": "m/s"},
                {"id": "current_v", "label": "Current (northward)", "unit": "m/s"},
            ],
            source="demo",
        )

    def field(self, time_idx: int, variable: str) -> np.ndarray:
        ti = max(0, min(time_idx, len(self.times) - 1))
        lat2, lon2, dep2 = np.meshgrid(self.lats, self.lons, self.depths, indexing="ij")
        phase = ti * 0.5
        depth_factor = np.exp(-dep2 / 900.0)

        if variable == "temperature":
            return (
                28.5 - 0.014 * dep2
                + 1.8 * np.sin(np.radians(lat2 * 5.0) + phase) * depth_factor
                + 0.8 * np.cos(np.radians(lon2 * 4.0) - phase)
            )
        if variable == "salinity":
            return (
                34.1 + 0.0016 * dep2
                + 0.65 * np.sin(np.radians(lon2 * 3.0) + phase)
                + 0.25 * np.cos(np.radians(lat2 * 4.0))
            )
        if variable == "chlorophyll":
            # Chlorophyll concentrates near the surface and along a coastal
            # upwelling-like gradient; decays sharply with depth.
            surface = 0.15 + 0.9 * np.clip(np.sin(np.radians(lon2 * 6.0) + phase) * np.cos(np.radians(lat2 * 3.0)), 0, None)
            return surface * np.exp(-dep2 / 60.0) + 0.02
        if variable == "current_u":
            return 0.45 * np.sin(np.radians(lat2 * 7.0) + phase) * depth_factor
        if variable == "current_v":
            return 0.35 * np.cos(np.radians(lon2 * 5.0) - phase) * depth_factor
        raise KeyError(variable)


class NetCDFAdapter:
    """Reads a real CF-convention-ish NetCDF ocean model file via xarray.

    Enable it by placing a file at data/ocean.nc. This is the extension
    point for INCOIS LAS / Copernicus Marine downloads
    (see data/README_DATA.md for the exact dataset links from the PS).
    """

    def __init__(self, path: Path) -> None:
        import xarray as xr  # imported lazily so the demo path has zero extra deps

        self.ds = xr.open_dataset(path)
        self._dim_names = {k: self._resolve(list(self.ds.dims) + list(self.ds.coords), v) for k, v in DIM_ALIASES.items()}
        self._var_names = {k: self._resolve(list(self.ds.data_vars), v) for k, v in VARIABLE_ALIASES.items()}

        lat_name = self._dim_names.get("lat")
        lon_name = self._dim_names.get("lon")
        depth_name = self._dim_names.get("depth")
        time_name = self._dim_names.get("time")
        if not (lat_name and lon_name):
            raise ValueError("NetCDF file is missing recognizable lat/lon coordinates")

        self.lats = np.asarray(self.ds[lat_name].values, dtype=float)
        self.lons = np.asarray(self.ds[lon_name].values, dtype=float)
        self.depths = np.asarray(self.ds[depth_name].values, dtype=float) if depth_name else np.array([0.0])
        if time_name:
            import pandas as pd

            self.times = [str(pd.Timestamp(t).isoformat()) for t in self.ds[time_name].values]
        else:
            self.times = ["static"]
        self._lat_name, self._lon_name, self._depth_name, self._time_name = lat_name, lon_name, depth_name, time_name

    @staticmethod
    def _resolve(available: list[str], candidates: list[str]) -> str | None:
        lower = {a.lower(): a for a in available}
        for c in candidates:
            if c.lower() in lower:
                return lower[c.lower()]
        return None

    def metadata(self) -> GridMeta:
        variables = []
        labels = {"temperature": ("Temperature", "°C"), "salinity": ("Salinity", "PSU"),
                   "chlorophyll": ("Chlorophyll-a", "mg/m³"), "current_u": ("Current (eastward)", "m/s"),
                   "current_v": ("Current (northward)", "m/s")}
        for key, ncname in self._var_names.items():
            if ncname:
                label, unit = labels[key]
                variables.append({"id": key, "label": label, "unit": unit})
        return GridMeta(lats=self.lats, lons=self.lons, depths=self.depths, times=self.times,
                         variables=variables, source=str(getattr(self.ds, "encoding", {}).get("source", "netcdf")))

    def field(self, time_idx: int, variable: str) -> np.ndarray:
        ncname = self._var_names.get(variable)
        if not ncname:
            raise KeyError(variable)
        da = self.ds[ncname]
        sel: dict[str, Any] = {}
        if self._time_name and self._time_name in da.dims:
            sel[self._time_name] = min(time_idx, da.sizes[self._time_name] - 1)
        arr = da.isel(**sel).values if sel else da.values

        # Normalise dimension order to (lat, lon, depth).
        dims = [d for d in da.dims if d != self._time_name]
        order = []
        for target in (self._lat_name, self._lon_name, self._depth_name):
            if target and target in dims:
                order.append(dims.index(target))
        if len(order) == len(dims):
            arr = np.moveaxis(arr, order, range(len(order)))
        if self._depth_name is None or arr.ndim == 2:
            arr = arr[:, :, None]
        return np.asarray(arr, dtype=float)


def build_adapter(data_dir: Path):
    """Factory: use a real NetCDF file if present and readable, else the demo dataset."""
    nc_path = data_dir / "ocean.nc"
    if nc_path.exists():
        try:
            return NetCDFAdapter(nc_path)
        except Exception as exc:  # pragma: no cover - defensive fallback, logged not swallowed silently
            print(f"[data_adapters] Failed to load {nc_path} ({exc}); falling back to demo dataset")
    return DemoAdapter()


# ---------------------------------------------------------------------------
# Observation (Argo / Glider / CTD / BGC) ingestion
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    id: str
    type: str
    lat: float
    lon: float
    timestamp: str
    depths: list[float] = dc_field(default_factory=list)
    temperature: list[float] = dc_field(default_factory=list)
    salinity: list[float] = dc_field(default_factory=list)
    track: list[dict[str, float]] = dc_field(default_factory=list)  # prior fixes, Gliders/Argo drift


def _synthetic_profile(seed: int, depths: list[float]) -> tuple[list[float], list[float]]:
    temp = [round(28.0 - 0.014 * d + 0.7 * math.sin(seed + d / 150), 2) for d in depths]
    sal = [round(34.2 + 0.0016 * d + 0.15 * math.cos(seed + d / 200), 2) for d in depths]
    return temp, sal


def _synthetic_track(typ: str, lat: float, lon: float, seed: int, times: list[str]) -> list[dict[str, float]]:
    """Gliders sample along a saw-tooth dive track; synthesize a short trailing
    surface track so the frontend can draw its recent path. Used by both the
    built-in demo set and any CSV-loaded Glider row, so track data survives
    either ingestion path."""
    if typ != "Glider":
        return []
    track: list[dict[str, float]] = []
    for step in range(6):
        track.append({
            "lat": round(lat - 0.28 * step + 0.03 * math.sin(seed + step), 3),
            "lon": round(lon - 0.22 * step, 3),
            "timestamp": times[max(0, len(times) - 6 + step) % len(times)] if times else "",
        })
    return track


def demo_observations(times: list[str]) -> list[Observation]:
    rows = [
        ("ARGO-2901", "Argo", 10.8, 72.5),
        ("ARGO-3147", "Argo", 15.2, 78.8),
        ("ARGO-5590", "Argo", 8.6, 76.1),
        ("GLIDER-07", "Glider", 18.4, 82.0),
        ("GLIDER-12", "Glider", 13.9, 80.6),
        ("CTD-IND-04", "CTD", 12.1, 84.2),
        ("BGC-22", "BGC", 20.2, 75.7),
    ]
    depths = [0, 25, 50, 100, 200, 400, 700, 1200, 2000]
    out: list[Observation] = []
    for i, (oid, typ, lat, lon) in enumerate(rows):
        temp, sal = _synthetic_profile(i, depths)
        track = _synthetic_track(typ, lat, lon, i, times)
        out.append(Observation(id=oid, type=typ, lat=lat, lon=lon, timestamp=times[i % len(times)],
                                depths=depths, temperature=temp, salinity=sal, track=track))
    return out


def load_observations_csv(path: Path, fallback: list[Observation]) -> list[Observation]:
    if not path.exists():
        return fallback
    out: list[Observation] = []
    depths = fallback[0].depths if fallback else [0, 25, 50, 100, 200, 400, 700]
    fallback_times = sorted({o.timestamp for o in fallback}) or [""]
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            temp, sal = _synthetic_profile(i, depths)
            typ = row["type"]
            lat, lon = float(row["lat"]), float(row["lon"])
            track = _synthetic_track(typ, lat, lon, i, fallback_times)
            out.append(Observation(
                id=row["id"], type=typ, lat=lat, lon=lon,
                timestamp=row.get("timestamp", ""), depths=depths, temperature=temp, salinity=sal,
                track=track,
            ))
    return out or fallback
