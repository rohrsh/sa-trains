"""
ARTC Master Train Plan PDF parser — text-extraction based.

The ARTC MTP PDFs use a tabular layout that pdfplumber's table extractor
compresses poorly (multiple station rows get merged). Text extraction works
cleanly: each row appears as a line with station name + arr/dep marker +
space-separated times (one per train column).

Each page has exactly 10 train columns. Trains run on a specific day per week.

Usage:
    python parser.py MTP_2026-04-19_VIC-SA-300.pdf MTP_2026-04-19_VIC-SA-350.pdf
Output: data/trains.json
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# Stations to record (in inbound order, Murray Bridge → Keswick)
STATIONS_INBOUND = [
    "Murray Bridge",
    "Monarto South",
    "Callington",
    "Petwood",
    "Mt Barker Junction",
    "Balhannah",
    "Ambleside",
    "Mt Lofty",
    "Belair",
    "Goodwood",
    "Keswick",
]

# For the app's display, show a subset in station-picker
STATIONS_DISPLAY = [
    "Murray Bridge",
    "Balhannah",
    "Mt Lofty",
    "Belair",
    "Keswick",
]

STATION_ALIASES = {
    "murray bridge": "Murray Bridge",
    "monarto south": "Monarto South",
    "callington": "Callington",
    "petwood": "Petwood",
    "mt barker junction": "Mt Barker Junction",
    "mt barker jctn": "Mt Barker Junction",
    "balhannah": "Balhannah",
    "ambleside": "Ambleside",
    "mt lofty": "Mt Lofty",
    "belair": "Belair",
    "goodwood": "Goodwood",
    "keswick": "Keswick",
}

TABLE_DIRECTION = {
    "VIC-SA-300": "inbound",
    "VIC-SA-350": "outbound",
}

HEADER_KEYWORDS = {
    "ARTC",
    "FORMS",
    "SCHEDULE",
    "PATH",
    "Effective",
    "Master Train Plan",
    "Dimboola Loop",
    "Adelaide (Keswick)",
    "PAGE",
}

TIME_RE = re.compile(r"^\+?(\d{1,2}):(\d{2})$")
STATION_ARR_RE = re.compile(r"^(.+?)\s+arr\s*(.*)$")
DEP_RE = re.compile(r"^dep\s+(.*)")


def _parse_time_str(s: str) -> str | None:
    """Return 'HH:MM' or '+HH:MM' from various raw strings."""
    if not s or s in ("-", ".", ""):
        return None
    s = s.strip()
    next_day = s.startswith("+")
    clean = s.lstrip("+")
    m = re.match(r"^(\d{1,2}):(\d{2})$", clean)
    if not m:
        return None
    return f"{'+' if next_day else ''}{int(m.group(1)):02d}:{m.group(2)}"


def _minutes(time_str: str) -> int | None:
    """Convert 'HH:MM' to minutes since midnight. '+HH:MM' → +1440."""
    if not time_str:
        return None
    nd = time_str.startswith("+")
    clean = time_str.lstrip("+")
    m = re.match(r"^(\d{1,2}):(\d{2})$", clean)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + (1440 if nd else 0)


def _normalise_station(name: str) -> str | None:
    key = name.lower().strip()
    for alias, canonical in STATION_ALIASES.items():
        if key == alias or key.startswith(alias):
            return canonical
    return None


def _is_header_line(line: str) -> bool:
    for kw in HEADER_KEYWORDS:
        if kw in line:
            return True
    return False


def parse_page(text: str, table_name: str, direction: str) -> list[dict]:
    lines = text.strip().split("\n")

    # --- Parse header rows ---
    train_ids: list[str] = []
    lengths: list[str] = []
    days_list: list[str] = []
    schedules: list[str] = []
    operators: list[str] = []
    commodities: list[str] = []

    # Station time data: station → [time_str_or_None, ...] (one per column)
    station_cols: dict[str, list[str | None]] = {}
    # Preserve insertion order (= geographic order)
    station_order: list[str] = []
    current_station: str | None = None

    for line in lines:
        s = line.strip()
        if not s:
            continue

        if s.startswith("TRAIN NO"):
            # "TRAIN NO 2MA8 2MA5 ..."
            train_ids = s.split()[2:]

        elif s.startswith("LENGTH"):
            # "LENGTH (Metres) 250 1500 ..."
            parts = s.split()
            lengths = [p for p in parts if re.match(r"^\d{3,4}$", p)]

        elif s.startswith("DAYS"):
            # "DAYS MON MON TUE ..."
            days_list = s.split()[1:]

        elif s.startswith("OPERATOR"):
            operators = s.split()[1:]

        elif s.startswith("COMMODITY"):
            commodities = s.split()[1:]

        elif s.startswith("SCHEDULE"):
            schedules = s.split()[1:]

        elif s.startswith("PATH TYPE"):
            continue  # ignore

        elif s.startswith("dep ") or s == "dep":
            # Departure times for current_station — only take the FIRST dep line.
            # Subsequent dep lines belong to unrecognised stations further down the route
            # (e.g. Victorian stations after Murray Bridge in the outbound table).
            m = DEP_RE.match(s)
            if m and current_station and current_station not in station_cols:
                raw_times = m.group(1).split()
                parsed = [_parse_time_str(t) for t in raw_times]
                station_cols[current_station] = parsed

        elif "arr" in s and not _is_header_line(s):
            # Station line: "Mt Lofty arr" or "Murray Bridge arr 16:08"
            m = STATION_ARR_RE.match(s)
            if m:
                raw_name = m.group(1).strip()
                canonical = _normalise_station(raw_name)
                if canonical:
                    current_station = canonical
                    if canonical not in station_cols:
                        station_order.append(canonical)

        elif s.startswith("FORMS OR"):
            break

    if not train_ids:
        return []

    # --- Build one train object per column ---
    trains: list[dict] = []

    for col, tid in enumerate(train_ids):
        if col >= len(days_list):
            break

        train: dict = {
            "train_id": tid,
            "table": table_name,
            "direction": direction,
            "length_m": int(lengths[col]) if col < len(lengths) else None,
            "schedule": schedules[col] if col < len(schedules) else "",
            "commodity": commodities[col] if col < len(commodities) else "",
            "operator": operators[col] if col < len(operators) else "",
            "days": [days_list[col]] if col < len(days_list) else [],
            "stations": {},
        }

        # Extract times, tracking day rollovers per train
        prev_raw_min: int | None = None
        day_offset = 0

        for station in station_order:
            col_times = station_cols.get(station, [])
            if col >= len(col_times):
                continue
            raw_t = col_times[col]
            if not raw_t:
                continue

            raw_min = _minutes(raw_t)
            if raw_min is None:
                continue

            # Detect rollover: if this station's time is >8 h earlier than the previous
            if prev_raw_min is not None and raw_min < prev_raw_min - 8 * 60:
                day_offset += 1440

            adjusted = raw_min + day_offset
            if adjusted >= 1440:
                hh = (adjusted % 1440) // 60
                mm = (adjusted % 1440) % 60
                train["stations"][station] = f"+{hh:02d}:{mm:02d}"
            else:
                hh = adjusted // 60
                mm = adjusted % 60
                train["stations"][station] = f"{hh:02d}:{mm:02d}"

            prev_raw_min = raw_min

        # Only keep trains with at least one Belair-line station
        belair_stations = set(STATIONS_INBOUND)
        if any(s in belair_stations for s in train["stations"]):
            trains.append(train)

    return trains


def parse_artc_pdf(filepath: str, filename: str | None = None) -> list[dict]:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber not installed: pip install pdfplumber")

    filename = filename or os.path.basename(filepath)
    stem = Path(filename).stem.upper()

    # Determine table name and direction
    table_name = "VIC-SA-300"
    direction = "inbound"
    for key, dir_ in TABLE_DIRECTION.items():
        if key in stem:
            table_name = key
            direction = dir_
            break

    all_trains: list[dict] = []

    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            page_trains = parse_page(text, table_name, direction)
            all_trains.extend(page_trains)

    return all_trains


def main(pdf_paths: list[str], data_file: str = "data/trains.json") -> None:
    all_trains: list[dict] = []

    for path in pdf_paths:
        print(f"Parsing {path} …")
        try:
            trains = parse_artc_pdf(path)
            print(f"  → {len(trains)} trains")
            if trains:
                # Show sample
                t = trains[0]
                print(f"     Sample: {t['train_id']} ({t['days']}) {t['length_m']}m {t['commodity']}")
                for s, v in list(t['stations'].items())[:5]:
                    print(f"       {s}: {v}")
            all_trains.extend(trains)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            import traceback; traceback.print_exc()

    if not all_trains:
        print("No trains extracted.")
        return

    print(f"\nTotal trains: {len(all_trains)}")
    schedules_found = sorted(set(t.get("schedule", "") for t in all_trains))
    print(f"Schedules: {schedules_found}")

    # Try to extract MTP effective date from the first filename (e.g. MTP_2026-04-19_VIC-SA-300.pdf)
    mtp_date = None
    for fp in pdf_paths:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(fp))
        if m:
            mtp_date = m.group(1)
            break

    data = {
        "source": "ARTC Master Train Plan — VIC-SA 300 / VIC-SA 350",
        "disclaimer": "Scheduled paths from ARTC MTP. Trains can be cancelled, late, or altered. Verify at artc.com.au.",
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "mtp_effective": mtp_date,
        "stations_order": STATIONS_DISPLAY,
        "trains": all_trains,
    }

    os.makedirs(os.path.dirname(data_file) or ".", exist_ok=True)
    with open(data_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Saved to {data_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
