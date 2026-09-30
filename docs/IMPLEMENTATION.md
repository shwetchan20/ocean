# Architecture notes

## Backend (`backend/`)

`main.py` is a thin FastAPI layer over `data_adapters.py`. It never touches
raw NetCDF/CSV parsing itself — that's the adapters' job — so the API
contract stays stable regardless of data source.

Field slices are cached per `(time_index, variable)` with `functools.lru_cache`
storing the raw numpy bytes; scrubbing the depth slider re-slices an
already-fetched 3D array instead of recomputing it.

`contouring.py` is dependency-free (pure numpy) marching squares. It's used
both directly by `/api/isosurface` and is unit-testable in isolation — see
its docstring for the algorithm's exact scope and the two synthetic-field
checks it was verified against during development (a radial field recovering
the expected contour radius to 3 decimal places, and a two-peak field
correctly returning two disjoint polylines).

## Frontend (`frontend/assets/app.js`)

No build step — ES modules loaded via `importmap` straight from a CDN
(`three@0.179.1`). Coordinate mapping is dynamic: `fitMapping()` derives the
lon/lat → scene-x/z scale and the depth → scene-y normalization from
whatever `/api/metadata` returns, so the same code works unmodified against
the demo grid or a real NetCDF file with a different extent.

Each active field layer (temperature/salinity/chlorophyll) renders as its
own `THREE.Points` cloud at the selected depth slice, with a tiny per-layer
Y offset to avoid z-fighting when multiple layers are shown at once. The
primary layer (first of temperature → salinity → chlorophyll that's
checked) drives the colorbar editor; secondary layers use fixed palettes so
they stay visually distinct without a second editor UI.

Current vectors use `THREE.ArrowHelper` — no custom instancing — for
correctness over cleverness at this scale (a stride-2 downsampled grid, a
few hundred arrows).
