# The summer jobs factor map

A two-measure (bivariate) map for weighing where New York City's Summer Youth Employment Program (SYEP) could do more. Pair any two of 25 measures: young people (16 to 24, under 18, under 25, the 2020 count of 14- to 24-year-olds), their poverty, unemployment, schooling and disconnection from school and work, 2024 arrests of people under 25, shootings and violent crime involving young people, and summer jobs program sites within a mile.

The map opens on **NYPD police precincts**, where every arrest, shooting and violent crime counts by the precinct NYPD assigned it, including the quarter of youth arrests NYPD logs at a station house without a street location. A switch shows **City Planning's 262 neighborhoods**, which are finer but leave those station-house records out. Census figures for precincts are split from tracts by 2020 census block population.

**Live:** https://vital-city-nyc.github.io/syep-factor-map/
**Methodology:** https://vital-city-nyc.github.io/syep-factor-map/methodology.html
**How I built this (every step and judgment call):** https://vital-city-nyc.github.io/syep-factor-map/how-i-built-this.html

Companion tools: [The summer jobs gap](https://vitalcity-nyc.github.io/syep-smart-scale/) and [What the evidence shows](https://vital-city-nyc.github.io/SYEP_evidence_tracker/).

## Rebuilding the data

```
export CENSUS_API_KEY=...        # free key; the 2020 census pull needs it
python3 fetch_youth.py           # station houses, NYPD arrests/shootings/complaints, DYCD sites, 2020 ages
python3 fetch_precincts.py       # precinct boundaries, 2020 blocks -> tract shares by precinct, NYPD counts by precinct
python3 fetch_crime.py           # 12-month major-felony counts (optional; feeds the daytime-crowd caveat)
python3 build.py                 # ACS tables (cached in cache/; --refresh to re-download) -> docs/
python3 scripts_check_presets.py # checks every place, count and correlation the preset texts name
```

Python 3 with `shapely` (and `pyproj` for the shootings coordinate check). Every pull stops rather than write an empty or implausible file.

## What gets checked

- Every Census table cell behind the young-people measures is checked against the Census Bureau's own label at build time.
- Neighborhood values were compared with NYC City Planning's published 2020–24 neighborhood tables.
- NYPD and DYCD totals reconcile with the source files; the map's sums equal them.
- NYPD records placed at a precinct station house (un-geocodable arrests, nearly every rape complaint, some shootings) count in the precinct view and are left out of the neighborhood view; no record is ever assigned to a neighborhood by its precinct. See `nypd_places.py` and the methodology page.
- Precinct sums equal the city totals exactly (residents and every NYPD count).

## Files

- `docs/index.html` — the map (Leaflet + d3, no build step)
- `docs/methodology.html` — sources, formulas, checks and limits
- `docs/precincts.geojson`, `docs/ntas.geojson` — the 25 measures for 78 precincts and 262 neighborhoods
- `data/precincts.geojson`, `data/tract_precinct_weights.json`, `data/precinct_counts.json` — precinct inputs from `fetch_precincts.py`
- `docs/youth_by_tract.json`, `docs/syep_sites.json` — NYPD and DYCD counts per tract, and the 122 program sites
- `data/stations.json` — NYPD station houses with their source pages
