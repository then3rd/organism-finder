"""Shared helpers: ArcGIS REST paging + HTTP retry.

Paths live in paths.py and the working projection on the Region record, because stage 05
needs both under the system interpreter and cannot import requests.
"""
from pathlib import Path
import json
import time

import requests

from paths import OUT, RAW, ROOT, WORK  # noqa: F401 - re-exported for the stages

# LANDFIRE CONUS grid. Region-independent, unlike the working CRS.
CRS_LF = "EPSG:5070"

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "blm-tree-overlay/2.0"


def get(url, params=None, timeout=180, retries=4, stream=False):
    """GET with linear backoff; BLM/USGS endpoints time out intermittently."""
    last = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout, stream=stream)
            # 4xx is the server telling us the request is wrong, not that it is busy;
            # backing off four times only delays the real error message.
            if 400 <= r.status_code < 500:
                raise RuntimeError(f"HTTP {r.status_code} for {url}")
            r.raise_for_status()
            return r
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - retry anything transient
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url} :: {last}")


def esri_features(layer_url, where="1=1", out_fields="*", page=1000, bbox=None):
    """Page an ArcGIS FeatureServer/MapServer layer into one GeoJSON FeatureCollection.

    `bbox` is `(minx, miny, maxx, maxy)` in WGS84 and narrows the query server-side. The
    state-office layers do not need it - they only hold one state - but the fire and
    hydrography services are national, and downloading the country to keep Utah is not a
    reasonable thing to do to somebody's connection.
    """
    feats = []
    offset = 0
    envelope = None
    if bbox is not None:
        minx, miny, maxx, maxy = bbox
        envelope = json.dumps({
            "xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
            "spatialReference": {"wkid": 4326},
        })
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
        if envelope is not None:
            params.update({
                "geometry": envelope,
                "geometryType": "esriGeometryEnvelope",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
            })
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


def esri_count(layer_url, where="1=1", bbox=None):
    params = {"where": where, "returnCountOnly": "true", "f": "json"}
    if bbox is not None:
        minx, miny, maxx, maxy = bbox
        params.update({
            "geometry": json.dumps({
                "xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                "spatialReference": {"wkid": 4326},
            }),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        })
    return get(f"{layer_url}/query", params=params).json()["count"]


def dump_json(obj, path):
    Path(path).write_text(json.dumps(obj))
