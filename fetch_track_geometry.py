"""
Fetch the actual rail track geometry from OpenStreetMap (via Overpass API)
and save it as data/track_geometry.json for the map view to draw.

The Adelaide → Wolseley line is tagged in OSM as a route relation. We pull all
the constituent ways and reconstruct an ordered polyline by chaining shared
nodes — Overpass returns ways as a soup, not in route order.

Run once after a fresh checkout; commit the JSON; the static site loads it
directly. No runtime Overpass dependency.
"""

import json
import ssl
import sys
import urllib.parse
import urllib.request

# macOS python3 ships without a CA bundle; relax verification.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# We bound the query to the Adelaide → Murray Bridge corridor for now (matching
# our timetable data). North bound is just above Keswick (-34.94) so we exclude
# Adelaide CBD ways — otherwise the chain walks north to the city instead of
# south to Belair. Extending east is just a bbox bump.
BBOX = (-35.20, 138.55, -34.94, 139.35)   # (south, west, north, east)

# We want standard-gauge mainline (the ARTC interstate corridor).
# Adelaide suburban services use broad gauge (1600mm); we exclude them by
# requiring gauge=1435 OR usage=main. We also exclude sidings/yards.
QUERY = f"""
[out:json][timeout:60];
(
  way["railway"="rail"]
     ["service"!~"."]
     ["usage"!="industrial"]
     ({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
out geom;
"""


def fetch():
    # Overpass accepts the query as a POST body OR a GET query string.
    # POST sometimes 406s through certain proxies; GET is universal.
    url = OVERPASS_URL + "?data=" + urllib.parse.quote(QUERY)
    req = urllib.request.Request(url, headers={
        "User-Agent": "sa-trains/1.0 (https://github.com/rohrsh/sa-trains)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as r:
        return json.loads(r.read())


def to_segments(elements):
    """Each way → list of [lat, lon] points. Returns [(way_id, points), ...]."""
    segs = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        pts = [[p["lat"], p["lon"]] for p in geom]
        segs.append((el["id"], pts))
    return segs


def near(a, b, eps=1e-5):
    """Two points considered 'connected' if within ~1m (~1e-5 deg)."""
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def chain_segments(segs, start_hint, end_hint):
    """
    Greedy walk from start_hint, preferring branches that move toward end_hint
    at every junction. Returns the longest connected polyline found.
    """
    if not segs:
        return []

    def d2(p, q):
        return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2

    # Seed: segment whose endpoint is closest to start_hint
    seeded = min(segs, key=lambda s: min(d2(s[1][0], start_hint), d2(s[1][-1], start_hint)))
    remaining = [s for s in segs if s[0] != seeded[0]]
    chain = seeded[1][:]
    if d2(chain[-1], start_hint) < d2(chain[0], start_hint):
        chain.reverse()

    changed = True
    while changed and remaining:
        changed = False
        tail = chain[-1]
        # Collect ALL candidates that connect to the tail (junctions can have several)
        cands = []
        for i, (wid, pts) in enumerate(remaining):
            if near(pts[0], tail):    cands.append((i, pts, False))
            elif near(pts[-1], tail): cands.append((i, pts, True))
        if not cands:
            continue
        # Among candidates, pick the one whose OTHER end is closest to end_hint
        def far_end(pts, reversed_): return pts[0] if reversed_ else pts[-1]
        i, pts, rev = min(cands, key=lambda c: d2(far_end(c[1], c[2]), end_hint))
        extension = list(reversed(pts[:-1])) if rev else pts[1:]
        chain.extend(extension)
        remaining.pop(i)
        changed = True

    return chain


def main():
    print(f"Fetching rail geometry from Overpass…")
    data = fetch()
    segs = to_segments(data.get("elements", []))
    print(f"  → {len(segs)} ways returned")

    # Chain from Keswick toward Murray Bridge — at each junction we pick the
    # branch whose far end is closest to Murray Bridge.
    KESWICK       = [-34.93806, 138.58111]
    MURRAY_BRIDGE = [-35.117,   139.267]
    chain = chain_segments(segs, KESWICK, MURRAY_BRIDGE)
    print(f"  → chained {len(chain)} points")

    if len(chain) < 100:
        print("WARNING: chain is short — Overpass may have returned partial data, "
              "or there are unconnected branches in the OSM tagging.")

    out = {
        "source": "OpenStreetMap (via Overpass API)",
        "license": "ODbL",
        "bbox": BBOX,
        "points": chain,   # [[lat, lon], …]
    }
    path = "data/track_geometry.json"
    with open(path, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"  Saved → {path}  ({len(chain)} points)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
