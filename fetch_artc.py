"""
Fetch the latest ARTC MTP PDFs for the Adelaide Hills (Belair) line and
re-parse the timetable data.

Discovers the current MTP edition from the ARTC website automatically —
no hardcoded dates needed.

Usage:
    python fetch_artc.py              # fetch + parse + save to data/trains.json
    python fetch_artc.py --check      # print the latest MTP date, don't save
"""

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from html.parser import HTMLParser

import requests

MTP_INDEX = "https://www.artc.com.au/customers/operations/mtp/"
BASE_URL = "https://www.artc.com.au"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BelairLineTracker/1.0; +https://github.com)"
}
TARGET_PDFS = ("VIC-SA-300", "VIC-SA-350")


class _HrefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href:
                self.hrefs.append(href)


def _get_with_retry(url: str, *, timeout: int = 60, attempts: int = 3) -> requests.Response:
    """GET with bounded retries — ARTC's site occasionally times out under load."""
    last_exc: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if i < attempts:
                wait = 5 * i   # 5s, 10s
                print(f"  attempt {i}/{attempts} failed ({type(exc).__name__}); retrying in {wait}s…")
                time.sleep(wait)
    raise RuntimeError(f"giving up on {url} after {attempts} attempts") from last_exc


def _get_hrefs(url: str) -> list[str]:
    r = _get_with_retry(url)
    p = _HrefParser()
    p.feed(r.text)
    return p.hrefs


def find_latest_mtp() -> tuple[str, str]:
    """
    Return (page_url, date_str) for the most recent MTP edition.
    Scrapes the index page for dated sub-pages like /mtp/2026-04-19/.
    """
    hrefs = _get_hrefs(MTP_INDEX)
    dates = []
    for h in hrefs:
        m = re.search(r"/mtp/(\d{4}-\d{2}-\d{2})/?", h)
        if m:
            dates.append(m.group(1))

    if not dates:
        raise ValueError("No dated MTP pages found — ARTC may have changed their URL structure")

    latest = sorted(set(dates))[-1]
    page_url = f"{BASE_URL}/customers/operations/mtp/{latest}/"
    return page_url, latest


def find_pdf_links(mtp_page_url: str) -> dict[str, str]:
    """
    Return {table_name: full_pdf_url} for VIC-SA-300 and VIC-SA-350.
    """
    hrefs = _get_hrefs(mtp_page_url)
    found: dict[str, str] = {}
    for h in hrefs:
        for target in TARGET_PDFS:
            if target in h:
                full = BASE_URL + h if h.startswith("/") else h
                found[target] = full
    return found


def download_pdf(url: str) -> str:
    """Download a PDF to a temp file and return the path."""
    r = _get_with_retry(url, timeout=120)
    suffix = os.path.basename(url)
    tmp = tempfile.NamedTemporaryFile(suffix=f"_{suffix}", delete=False)
    tmp.write(r.content)
    tmp.close()
    return tmp.name


def fetch_and_refresh(data_file: str = "data/trains.json") -> dict:
    """
    Full pipeline: discover latest MTP → download PDFs → parse → save JSON.
    Returns a summary dict with counts and metadata.
    """
    from parser import parse_artc_pdf, STATIONS_DISPLAY

    # 1. Discover latest edition
    mtp_url, date_str = find_latest_mtp()
    print(f"Latest MTP: {date_str} ({mtp_url})")

    # 2. Find PDF links
    pdf_links = find_pdf_links(mtp_url)
    if not pdf_links:
        raise ValueError(f"No VIC-SA PDF links found at {mtp_url}")
    print(f"PDFs found: {list(pdf_links.keys())}")

    # 3. Download + parse
    all_trains: list[dict] = []
    for table_name, url in pdf_links.items():
        fname = os.path.basename(url)
        print(f"  Downloading {fname} …")
        tmp = download_pdf(url)
        try:
            trains = parse_artc_pdf(tmp, fname)
            print(f"  → {len(trains)} trains parsed")
            all_trains.extend(trains)
        finally:
            os.unlink(tmp)

    print(f"Total paths: {len(all_trains)}")

    # 4. Save — all schedule types included; the app filters by schedule/commodity
    data = {
        "source": f"ARTC Master Train Plan — VIC-SA 300 / VIC-SA 350 (effective {date_str})",
        "disclaimer": (
            "Scheduled paths from ARTC MTP. "
            "Trains can be cancelled, late, or altered. "
            "Verify at artc.com.au."
        ),
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "mtp_effective": date_str,
        "stations_order": STATIONS_DISPLAY,
        "trains": all_trains,
    }

    os.makedirs(os.path.dirname(data_file) or ".", exist_ok=True)
    with open(data_file, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Saved → {data_file}")
    return {"trains": len(all_trains), "mtp_effective": date_str}


if __name__ == "__main__":
    if "--check" in sys.argv:
        url, date = find_latest_mtp()
        print(f"Latest MTP: {date}")
        print(f"  Page: {url}")
        pdfs = find_pdf_links(url)
        for name, link in pdfs.items():
            print(f"  {name}: {link}")
    else:
        fetch_and_refresh()
