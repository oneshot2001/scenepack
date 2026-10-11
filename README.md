# scenepack

An offline, checkable evidence package for crash-scene survey points.

You give it the surveyor's point file (PNEZD or PENZD CSV) and the facts about where it came from. It gives you back a zip that an outside analyst can check on a machine with no network: every file hashed, every coordinate conversion recorded as a recipe that can be re-run, and every point labeled for what it is. It can also write a KML so the points show up in Google Earth, labeled as an illustrative aid.

It is a v0 proof. It is not a diagramming tool, it does not read total-station binary formats, and it does not sign anything.

## What it proves

- The numbers in the package are the numbers that were imported, and the converted coordinates came from them by the recorded recipe (software versions, grid files and settings are in the package and can be re-run).
- A file in the package has not changed since it was packed (its hash matches the manifest).

## What it does not prove

- That the survey was done right, or that the instrument was calibrated. The tool never saw the instrument. (`field_accuracy: not_established`)
- Who handled the file before import, or after packing. (`custody_continuity: not_established`)
- That every point that was measured is in the file. (`completeness: not_established`)
- That the package you are holding is the one that was packed. A package rewritten wholesale, manifest included, passes `verify`. The control is the **package digest** printed by `pack`: record it somewhere the package cannot touch (the evidence log, an email to yourself, a notebook) and compare before you trust a copy.
- Anything about admissibility. (`legal_admissibility: not_established`)

A CSV holds coordinates, not the angles and distances the instrument observed. `verify` re-derives the exported coordinates from the imported ones. It cannot re-derive the survey.

## Coordinates, plainly

Surveys in Utah usually come in State Plane feet (Utah Central, NAD83(2011), US survey foot) with NAVD88 heights. Google Earth wants latitude, longitude and a height above mean sea level (EGM96). Getting from one to the other is four steps, and the tool writes all four into the package: feet to metres, the map projection inverse, the GEOID18 grid to go from NAVD88 to ellipsoid height, and the EGM96 grid to go from ellipsoid height to Google's sea level. PROJ treats NAD83(2011) and WGS84 as the same frame; in reality they differ by one to two metres in the US. The KML position carries that offset. The package says so in the recipe receipt.

Refusals are deliberate: the tool will not relabel a coordinate system to make an input fit (EPSG:2281 is international feet on the original NAD83, not US survey feet on NAD83(2011)), will not mix units, and will not write a KML for a scene with no control tie, because a local grid has no place on the Earth.

## Never say

Not in a report, a slide, or testimony prepared from this tool's output:

- "verified scene"
- "survey-grade"
- "court-ready"
- "chain of custody established"
- "measured from Google Earth"
- "complete record"
- "satisfies OSAC" or "compliant with ASB 226"

## Standards and rules it is written against

- OSAC 2023-N-0003 v2.1 (Standard for Diagramming Scenes, now ASB 226 in process): it asks for an estimated uncertainty, one unit system per output, preserved supporting data, and units, legend and north on every output. The package addresses those; it does not certify anything.
- Federal Rule of Evidence 107 (illustrative aids, effective 2024-12-01): the KML is intended as an illustrative aid. Its legal status is for the court.
- Google Maps Platform terms: the tool never fetches Google imagery and never measures from it. The KML is opened by you, in Google Earth, under Google's terms.

## Quick start

```
uv run scenepack init demo
uv run scenepack import demo tests/fixtures/slc-synthetic-ftus.csv --order PNEZD --h-unit ftUS --v-unit ftUS --h-crs EPSG:6625 --v-crs EPSG:6360
uv run scenepack transform demo
uv run scenepack export-kml demo
uv run scenepack pack demo            # prints the package digest. Write it down.
uv run scenepack verify demo/pack.zip
```

The test scene is synthetic (labeled `synthetic` on every point). No real case data is in this repository.

## Licensing

Code: MIT. Bundled grids: GEOID18 (`us_noaa_g2018u0.tif`, NOAA/NGS, public domain) and EGM96 (`us_nga_egm96_15.tif`, NGA, public domain), both as redistributed by PROJ-data; see NOTICE for hashes and sources.
