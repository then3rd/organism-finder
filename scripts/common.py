"""Shared helpers: ArcGIS REST paging + project paths."""
from pathlib import Path
import json
import time

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WORK = ROOT / "data" / "work"
OUT = ROOT / "out"
for _d in (RAW, WORK, OUT):
    _d.mkdir(parents=True, exist_ok=True)

# BLM Utah publishes everything in NAD83 / UTM 12N; stay in it so areas are metric.
CRS = "EPSG:26912"
# LANDFIRE CONUS grid.
CRS_LF = "EPSG:5070"

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "juniper-blm-overlay/1.0"


def get(url, params=None, timeout=180, retries=4, stream=False):
    """GET with linear backoff; BLM/USGS endpoints time out intermittently."""
    last = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout, stream=stream)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001 - retry anything transient
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url} :: {last}")


def esri_features(layer_url, where="1=1", out_fields="*", page=1000):
    """Page an ArcGIS FeatureServer/MapServer layer into one GeoJSON FeatureCollection."""
    feats = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page,
        }
        data = get(f"{layer_url}/query", params=params).json()
        if "error" in data:
            raise RuntimeError(f"{layer_url}: {data['error']}")
        batch = data.get("features", [])
        feats.extend(batch)
        if not data.get("properties", {}).get("exceededTransferLimit") and not data.get(
            "exceededTransferLimit"
        ):
            if len(batch) < page:
                break
        if not batch:
            break
        offset += len(batch)
        print(f"    ... {len(feats)} features", flush=True)
    return {"type": "FeatureCollection", "features": feats}


def esri_count(layer_url, where="1=1"):
    return get(
        f"{layer_url}/query",
        params={"where": where, "returnCountOnly": "true", "f": "json"},
    ).json()["count"]


def dump_json(obj, path):
    Path(path).write_text(json.dumps(obj))
