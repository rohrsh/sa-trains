import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DATA_FILE = Path(__file__).parent / "data" / "trains.json"

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def load_data() -> dict:
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "trains": [],
            "stations_order": ["Murray Bridge", "Bridgewater", "Mt Lofty", "Belair", "Goodwood", "Keswick"],
            "disclaimer": "No data file found. Upload ARTC MTP PDFs to populate.",
            "last_updated": None,
        }


def current_day() -> str:
    return DAYS[datetime.now().weekday()]


def parse_minutes(time_str: str) -> int | None:
    """Convert 'HH:MM' or '+HH:MM' to minutes since midnight (next-day times > 1440)."""
    if not time_str or time_str == "-":
        return None
    next_day = time_str.startswith("+")
    clean = time_str.lstrip("+")
    m = re.match(r"^(\d{1,2}):(\d{2})$", clean)
    if not m:
        return None
    total = int(m.group(1)) * 60 + int(m.group(2))
    return total + (1440 if next_day else 0)


def minutes_to_display(minutes: int) -> str:
    actual = minutes % 1440
    return f"{actual // 60:02d}:{actual % 60:02d}"


def compute_status(stations: dict, stations_order: list, now_min: int) -> dict:
    """Return status dict with status string, next_station, and minutes_away."""
    times = []
    for s in stations_order:
        t = parse_minutes(stations.get(s, ""))
        if t is not None:
            times.append((s, t))

    if not times:
        return {"status": "unknown", "next_station": None, "minutes_away": None, "sort_key": 9999}

    # Sort by time so outbound trains (Keswick first) are handled correctly
    times_sorted = sorted(times, key=lambda x: x[1])
    first_t = times_sorted[0][1]
    last_t = times_sorted[-1][1]

    # Find the next upcoming station in chronological order
    next_station = None
    minutes_away = None
    for station, t in times_sorted:
        diff = t - now_min
        if diff >= 0:
            next_station = station
            minutes_away = diff
            break

    if now_min > last_t + 45:
        # All stations passed
        return {"status": "passed", "next_station": None, "minutes_away": None, "sort_key": last_t + 1440}

    if next_station is None:
        return {"status": "passed", "next_station": None, "minutes_away": None, "sort_key": last_t + 1440}

    if minutes_away <= 30:
        status = "imminent"
    elif minutes_away <= 120:
        status = "upcoming"
    else:
        status = "later"

    return {
        "status": status,
        "next_station": next_station,
        "minutes_away": minutes_away,
        "sort_key": first_t if first_t >= now_min - 30 else first_t + 1440,
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/trains")
def api_trains():
    day = request.args.get("day") or current_day()
    direction = request.args.get("direction", "both")

    data = load_data()
    stations_order = data.get("stations_order", [])

    now = datetime.now()
    now_min = now.hour * 60 + now.minute

    results = []
    for train in data.get("trains", []):
        if day not in train.get("days", []):
            continue
        if direction != "both" and train.get("direction") != direction:
            continue

        info = compute_status(train.get("stations", {}), stations_order, now_min)
        entry = {**train, **info}
        results.append(entry)

    results.sort(key=lambda t: t["sort_key"])

    # Find "next at each station" summary (results already sorted by sort_key)
    next_at: dict[str, dict] = {}
    for s in stations_order:
        for t in results:
            if t["status"] == "passed":
                continue
            t_min = parse_minutes(t.get("stations", {}).get(s, ""))
            if t_min is not None and t_min >= now_min - 5:
                next_at[s] = {
                    "train_id": t["train_id"],
                    "time": minutes_to_display(t_min),
                    "minutes_away": t_min - now_min,
                    "length_m": t.get("length_m"),
                    "commodity": t.get("commodity"),
                    "direction": t.get("direction"),
                }
                break

    return jsonify({
        "trains": results,
        "day": day,
        "current_day": current_day(),
        "current_time": now.strftime("%H:%M"),
        "stations_order": stations_order,
        "next_at": next_at,
        "last_updated": data.get("last_updated"),
        "mtp_effective": data.get("mtp_effective"),
        "disclaimer": data.get("disclaimer", ""),
        "source": data.get("source", ""),
    })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Parse uploaded ARTC MTP PDFs and replace the train data."""
    try:
        from parser import parse_artc_pdf

        files = request.files.getlist("pdfs")
        if not files:
            return jsonify({"error": "No files uploaded"}), 400

        all_trains: list[dict] = []
        errors: list[str] = []

        for f in files:
            if not f.filename or not f.filename.lower().endswith(".pdf"):
                continue
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                f.save(tmp.name)
                try:
                    trains = parse_artc_pdf(tmp.name, f.filename)
                    all_trains.extend(trains)
                except Exception as exc:
                    errors.append(f"{f.filename}: {exc}")
                finally:
                    os.unlink(tmp.name)

        if not all_trains:
            return jsonify({"error": "No trains extracted", "details": errors}), 422

        data = load_data()
        data["trains"] = all_trains
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        data["disclaimer"] = "Parsed from uploaded ARTC MTP PDFs. Verify at artc.com.au."

        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DATA_FILE, "w") as fp:
            json.dump(data, fp, indent=2)

        return jsonify({"trains_loaded": len(all_trains), "warnings": errors})

    except ImportError:
        return jsonify({"error": "pdfplumber not installed"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Fetch latest ARTC MTP PDFs from artc.com.au and re-parse."""
    try:
        from fetch_artc import fetch_and_refresh
        result = fetch_and_refresh(str(DATA_FILE))
        return jsonify({"trains_loaded": result["trains"], "mtp_effective": result["mtp_effective"]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
