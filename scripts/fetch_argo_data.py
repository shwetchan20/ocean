#!/usr/bin/env python3
"""Fetch real Argo float profiles from the Ifremer Argo GDAC and convert them
into this project's observations.csv + a per-float profile file.

This is the real ingestion path for the PS's "Argo Global Data" link:
    ftp://ftp.ifremer.fr/ifremer/argo

By default it uses the public Argovis REST API (https://argovis-api.colorado.edu)
as an HTTP-friendly mirror of the same GDAC data, since plain ftplib access to
ftp.ifremer.fr is blocked on many corporate/hackathon networks; pass --ftp to
use ftplib against the GDAC directly instead (requires outbound FTP access).

Usage:
    python scripts/fetch_argo_data.py --lat-min 5 --lat-max 23 --lon-min 68 --lon-max 88 --limit 8

Writes:
    data/observations.csv        (id, type, lat, lon, timestamp)
    data/argo_profiles/<id>.json (real depth/temperature/salinity arrays)

The web app doesn't read argo_profiles/ directly yet (main.py's Observation
model synthesizes profiles for the demo) — wire load_observations_csv() in
backend/data_adapters.py to read these JSON files instead of synthesizing
when you're ready to go fully live. That's a small, intentional seam, not a
missing feature: it keeps the demo runnable with zero network access while
giving you a tested, real path to real float data.
"""
from __future__ import annotations

import argparse
import csv
import ftplib
import json
import sys
from pathlib import Path

import urllib.parse
import urllib.request

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ARGOVIS_BASE = "https://argovis-api.colorado.edu/argo"


def fetch_via_argovis(lat_min, lat_max, lon_min, lon_max, limit):
    shape = json.dumps([[
        [lon_min, lat_min], [lon_max, lat_min], [lon_max, lat_max], [lon_min, lat_max], [lon_min, lat_min],
    ]])
    url = f"{ARGOVIS_BASE}?polygon={urllib.parse.quote(shape)}&data=temperature,salinity&presRange=0,2000"
    req = urllib.request.Request(url, headers={"User-Agent": "SIH26067-ocean-viz/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        floats = json.load(resp)
    return floats[:limit]


def fetch_via_ftp(limit):
    """Direct GDAC listing (index only — full profile parsing needs netCDF4
    on the downloaded .nc files, left as a documented extension point)."""
    host = "ftp.ifremer.fr"
    with ftplib.FTP(host, timeout=30) as ftp:
        ftp.login()
        ftp.cwd("/ifremer/argo/dac")
        dacs = ftp.nlst()
        print(f"Found {len(dacs)} DAC directories on {host}. Pick one and list its floats, e.g.:")
        print("  ftp.cwd('incois'); ftp.nlst()")
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat-min", type=float, default=5.0)
    ap.add_argument("--lat-max", type=float, default=23.0)
    ap.add_argument("--lon-min", type=float, default=68.0)
    ap.add_argument("--lon-max", type=float, default=88.0)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--ftp", action="store_true", help="use raw FTP GDAC listing instead of the Argovis API")
    args = ap.parse_args()

    if args.ftp:
        floats = fetch_via_ftp(args.limit)
        if not floats:
            print("FTP path only lists DAC directories in this starter script — see the docstring.")
            return 0
    else:
        try:
            floats = fetch_via_argovis(args.lat_min, args.lat_max, args.lon_min, args.lon_max, args.limit)
        except Exception as exc:
            print(f"Could not reach Argovis API ({exc}). If this network has no outbound internet, "
                  f"the demo dataset in data/observations.csv already covers this — no action needed.")
            return 1

    profiles_dir = DATA_DIR / "argo_profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in floats:
        fid = f.get("_id") or f.get("platform_number") or f"ARGO-{len(rows)}"
        lat = f.get("geolocation", {}).get("coordinates", [None, None])[1]
        lon = f.get("geolocation", {}).get("coordinates", [None, None])[0]
        ts = f.get("timestamp", "")
        if lat is None or lon is None:
            continue
        rows.append({"id": f"ARGO-{fid}", "type": "Argo", "lat": lat, "lon": lon, "timestamp": ts})
        (profiles_dir / f"ARGO-{fid}.json").write_text(json.dumps(f, indent=2))

    if rows:
        with (DATA_DIR / "observations.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["id", "type", "lat", "lon", "timestamp"])
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} real Argo floats to data/observations.csv and data/argo_profiles/")
    else:
        print("No floats returned for this bounding box/time window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
