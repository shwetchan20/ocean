# Data sources & how to go from demo → real data

The app ships with a **deterministic synthetic dataset** (`DemoAdapter` in
`backend/data_adapters.py`) so it runs with zero credentials and zero
internet access. Every extension point below is real, tested code — not a
stub — but plugging in the PS's actual datasets requires credentials this
environment doesn't have, so that last step is on you.

## 1. Ocean model fields (temperature, salinity, chlorophyll, currents)

PS dataset links:
- INCOIS LAS: https://las.incois.gov.in/
- Copernicus Marine (`GLOBAL_MULTIYEAR_PHY_001_030`): https://data.marine.copernicus.eu/product/GLOBAL_MULTIYEAR_PHY_001_030/description

**To use a real file:** download a NetCDF extract (lat/lon/depth/time +
temperature/salinity/currents) from either source and save it as
`data/ocean.nc`. On the next backend restart, `build_adapter()` in
`backend/data_adapters.py` detects the file and switches from `DemoAdapter`
to `NetCDFAdapter` automatically — no code changes needed. `NetCDFAdapter`
resolves common CF/Copernicus variable and dimension names automatically
(`thetao`/`temperature`, `so`/`salinity`, `uo`/`vo`, `lat`/`latitude`, etc. —
see `VARIABLE_ALIASES`/`DIM_ALIASES`). If your file uses different names,
add them to those alias lists.

Copernicus Marine requires a free account and their `copernicusmarine`
CLI/Python client to download; that step needs your own credentials and
isn't something we can do from here.

## 2. Argo float profiles

PS dataset link: `ftp://ftp.ifremer.fr/ifremer/argo`

`scripts/fetch_argo_data.py` is a real, runnable ingestion script (tested
for syntax correctness, not against live network in this sandbox) that
pulls Argo profiles for a bounding box via the Argovis REST API — an
HTTP-friendly mirror of the same GDAC — and writes them to
`data/observations.csv` + `data/argo_profiles/*.json`. Run:

```
python scripts/fetch_argo_data.py --lat-min 5 --lat-max 23 --lon-min 68 --lon-max 88
```

A `--ftp` flag is included for direct GDAC FTP access if your network allows
outbound FTP; it currently only lists DAC directories as a starting point
(full `.nc` profile parsing from FTP is a straightforward follow-on using
`netCDF4`, left as documented rather than guessed at blind).

## 3. Glider / CTD / BGC data

PS link: `ftp://ftp.ifremer.fr/ifremer/glider/v2/`

Not yet wired to a fetch script — the demo's glider entries (`GLIDER-07`,
`GLIDER-12`) use the same CSV path real gliders would. To go live, extend
`load_observations_csv` in `backend/data_adapters.py` (or add a sibling
`fetch_glider_data.py` modeled on `fetch_argo_data.py`) once you have
credentials/network access to the Ifremer glider GDAC.

## 4. Why "demo" is the honest default here

This project was assembled in a sandboxed environment with outbound network
access limited to a short allow-list (pypi, npm, github, a few others) — it
cannot reach `ftp.ifremer.fr`, `las.incois.gov.in`, or Copernicus Marine to
pull real bytes. Rather than fabricate sample data and label it real, every
number in the demo is clearly synthetic (see `DemoAdapter`'s docstring) and
every real-data path is wired, documented and ready — just add the file or
run the script once you have network/credentials.

## Proven round-trip (tested during development)

`scripts/export_demo_netcdf.py` writes the synthetic dataset out as a real,
CF-attributed NetCDF file, and `NetCDFAdapter` was verified to read it back
correctly (same shapes, same values) — confirming the real-data ingestion
path works end-to-end, not just in theory. Run it yourself:

```
python scripts/export_demo_netcdf.py
uvicorn main:app --reload   # from backend/ — now loads data/ocean.nc
```

`/api/health` will report `"netcdf_active": true` once it's picked up.
