#!/usr/bin/env python3
"""Export the built-in synthetic dataset (DemoAdapter) as a real CF-ish
NetCDF file at data/ocean.nc.

Useful for: (1) proving the NetCDFAdapter ingestion path actually works
end-to-end without needing a real INCOIS/Copernicus download first, and
(2) as a template for the variable/dimension naming a real file should
follow if you want to skip touching VARIABLE_ALIASES/DIM_ALIASES in
backend/data_adapters.py.

Usage:
    python scripts/export_demo_netcdf.py
    # then restart the backend — it will pick up data/ocean.nc automatically
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402
from data_adapters import DemoAdapter  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "ocean.nc"


def main() -> int:
    adapter = DemoAdapter()
    meta = adapter.metadata()
    n_t = len(meta.times)

    data_vars = {}
    for var in ("temperature", "salinity", "chlorophyll", "current_u", "current_v"):
        stack = np.stack([adapter.field(t, var) for t in range(n_t)], axis=0)  # (time, lat, lon, depth)
        data_vars[var] = (("time", "lat", "lon", "depth"), stack)

    ds = xr.Dataset(
        data_vars,
        coords={
            "time": np.array(meta.times, dtype="datetime64[ns]"),
            "lat": meta.lats,
            "lon": meta.lons,
            "depth": meta.depths,
        },
        attrs={"title": "OceanScope 3D synthetic demo dataset", "source": "DemoAdapter", "Conventions": "CF-1.8"},
    )
    ds["lat"].attrs = {"standard_name": "latitude", "units": "degrees_north"}
    ds["lon"].attrs = {"standard_name": "longitude", "units": "degrees_east"}
    ds["depth"].attrs = {"standard_name": "depth", "units": "m", "positive": "down"}
    ds["temperature"].attrs = {"units": "degC", "standard_name": "sea_water_temperature"}
    ds["salinity"].attrs = {"units": "PSU", "standard_name": "sea_water_salinity"}
    ds["chlorophyll"].attrs = {"units": "mg m-3", "standard_name": "mass_concentration_of_chlorophyll_a"}
    ds["current_u"].attrs = {"units": "m s-1", "standard_name": "eastward_sea_water_velocity"}
    ds["current_v"].attrs = {"units": "m s-1", "standard_name": "northward_sea_water_velocity"}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(OUT)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB) — restart the backend to load it via NetCDFAdapter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
