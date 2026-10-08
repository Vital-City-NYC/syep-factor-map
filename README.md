# The summer jobs factor map

A two-measure (bivariate) map of New York City's neighborhoods and census tracts for weighing where the Summer Youth Employment Program (SYEP) could do more. Pair any two of 96 measures: young people (16 to 24, under 18, under 25, the 2020 count of 14- to 24-year-olds), their poverty, unemployment, schooling and disconnection from school and work, 2024 arrests of people under 25, shootings and violent crime involving young people, and where SYEP program sites are, plus every measure from the New York City population explorer it was built from.

**Live:** https://vital-city-nyc.github.io/syep-factor-map/
**Methodology:** https://vital-city-nyc.github.io/syep-factor-map/methodology.html
**How I built this (every step and judgment call):** https://vital-city-nyc.github.io/syep-factor-map/how-i-built-this.html

Companion tools: [The summer jobs gap](https://vitalcity-nyc.github.io/syep-smart-scale/) and [What the research says](https://vital-city-nyc.github.io/syep-crime-evidence/).

## Rebuilding the data

```
export CENSUS_API_KEY=...        # free key; the 2020 census pull needs it
python3 fetch_youth.py           # station houses, NYPD arrests/shootings/complaints, DYCD sites, 2020 ages
python3 fetch_crime.py           # 12-month major-felony counts (optional refresh)
python3 build.py                 # ACS tables (cached in cache/; --refresh to re-download) -> docs/
```

Python 3 with `shapely` (and `pyproj` for the shootings coordinate check). Every pull stops rather than write an empty or implausible file.

## What gets checked

- Every Census table cell behind the young-people measures is checked against the Census Bureau's own label at build time.
- Neighborhood values were compared with NYC City Planning's published 2020–24 neighborhood tables.
- NYPD and DYCD totals reconcile with the source files; the map's sums equal them.
- NYPD records placed at a precinct station house (un-geocodable arrests, nearly every rape complaint, some shootings) are spread across the precinct; see `nypd_places.py` and the methodology page.

## Files

- `docs/index.html` — the map (Leaflet + d3, no build step)
- `docs/methodology.html` — sources, formulas, checks and limits
- `docs/ntas.geojson`, `docs/tracts.geojson` — every measure for 262 neighborhoods and 2,325 tracts
- `docs/youth_by_tract.json`, `docs/syep_sites.json` — NYPD and DYCD counts per tract, and the 122 program sites
- `data/stations.json` — NYPD station houses with their source pages
