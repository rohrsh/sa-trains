# SA Trains

**ARTC freight timetable for the Adelaide Hills — because massive trains through the suburbs deserve a decent viewer**

The Belair line through the Adelaide Hills is one of Australia's most dramatic freight corridors: intermodal containers, steel slabs, and trail locomotives grind up steep grades through leafy suburbs on their way between Melbourne and Adelaide. Trains can stretch up to 1.8 km long, take nearly an hour to clear a level crossing, and run at all hours. This app turns the dry scheduled paths from ARTC's Master Train Plan into something a human can actually read — filter by station, direction, day, and train type, then tap any row to decode the cryptic ARTC train ID into plain English (operator, commodity, origin, destination).

## Live demo

[Add your GitHub Pages URL here]

## Features

- **Station picker** — select any of 14 stations from Murray Bridge to Keswick, including 9 intermediate stops with interpolated times (ARTC only publishes times for the major stations)
- **Direction, day, and type filters** — show only what you care about: inbound/outbound, weekday/weekend, intermodal/steel/passenger/trail loco
- **Expandable train-ID decoder** — click any row to see operator (Pacific National, SCT Logistics, Genesee & Wyoming, Great Southern Rail…), commodity, and route in plain English
- **Weekly auto-update** — GitHub Actions fetches the latest ARTC Master Train Plan every week and rebuilds `data/trains.json` automatically

## Data

Train times come from the [ARTC Master Train Plan (MTP)](https://www.artc.com.au/customers/operations/mtp/) — a quarterly PDF published by the Australian Rail Track Corporation that lists scheduled paths for every train on the network. This is **planned schedule data, not live tracking**: trains can run late, be cancelled, or have their paths altered without notice. The MTP covers two sections relevant to this app: VIC-SA 300 (Melbourne → Adelaide) and VIC-SA 350 (Adelaide → Melbourne).

The PDFs are downloaded on demand by `fetch_artc.py` and are not stored in this repository (they are copyrighted ARTC documents).

## Hosting on GitHub Pages

1. Fork or clone this repository to your own GitHub account
2. Go to **Settings → Pages**
3. Under *Build and deployment*, set source to **Deploy from a branch**, branch `main`, folder `/ (root)`
4. Click **Save** — your site will be live at `https://<your-username>.github.io/sa-trains/` within a minute or two

GitHub Actions handles the weekly data refresh automatically. For public repositories, Actions has write permission enabled by default — no extra configuration needed.

## Local development

```bash
pip install -r requirements.txt
python app.py        # Flask dev server at localhost:5001

# Or just serve the static files directly:
python -m http.server 8080
```

To manually refresh train data from the latest ARTC MTP:

```bash
python fetch_artc.py
```

This downloads the current MTP PDFs and runs `parser.py` to regenerate `data/trains.json`.

To refresh the map's track geometry from OpenStreetMap (rarely needed — track alignments don't change often):

```bash
python fetch_track_geometry.py
```

This queries the Overpass API for the rail ways in the Adelaide → Murray Bridge bounding box, chains them into an ordered polyline starting at Keswick, and writes `data/track_geometry.json`. The map view loads this for the blue track line and to snap train-position dots to the real track.

## Roadmap

- **Other ARTC corridors** — VIC-NSW (Sydney–Melbourne via the Southern Highlands), WA (Trans-Australian, Fremantle port), QLD coal networks
- **Live train positions** — if ARTC ever publishes a real-time feed, overlay actual positions on the timetable
- **Noise/vibration alerts** — Web Notifications API push when a train is due at your nearest station (great for sleeping with the window open)
- **Passenger timetables** — Adelaide Metro and The Overland alongside freight paths for a complete picture of the line

## Contributing

PRs are very welcome, especially improvements to `parser.py` for handling edge cases in MTP formatting, or new corridor parsers. If you live near another ARTC corridor and want to add it, open an issue first so we can agree on a data structure — the goal is to keep the same frontend working across corridors.

## License

MIT — see [LICENSE](LICENSE).

---

*Scheduled paths only. Not affiliated with, endorsed by, or connected to the Australian Rail Track Corporation (ARTC) in any way.*
