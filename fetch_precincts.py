#!/usr/bin/env python3
"""Precinct inputs for the summer jobs factor map (added Oct. 9, 2026).

The precinct view counts EVERY NYPD record by the precinct NYPD assigned it, so the
records NYPD logs at a station house (left out of the neighborhood view because their
street location is unknown) are included. Census figures, which come by tract, are
split along precinct lines using 2020 census block populations.

Run after fetch_youth.py: it reuses the time windows recorded in
docs/youth_by_tract.json so the two views describe the same arrests, shootings and
complaints.

Sources (all free, no key):
  Police precincts   NYC Dept. of City Planning, NYC Open Data y76i-bdw7 (shoreline-clipped)
  Census blocks      Census TIGERweb, tigerWMS_Census2020 layer 10: each 2020 block's
                     population (POP100), land area and internal point
  NYPD               the same datasets as fetch_youth.py, grouped by precinct field

Outputs:
  data/precincts.geojson           boundaries
  data/tract_precinct_weights.json share of each tract's 2020 residents in each precinct
  data/precinct_counts.json        youth arrests, shootings and violent crimes by precinct
"""
import datetime as dt
import json
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, shape

from fetch_youth import http, soql, soql_all, iso, NYPD_JURISDICTIONS, VIOLENT_NO_RAPE, NYC_COUNTIES

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
TIGER_BLOCKS = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
                "tigerWMS_Census2020/MapServer/10/query")
MAX_BAD_PRECINCT_SHARE = 0.005


def fetch_precincts():
    gj = http("https://data.cityofnewyork.us/resource/y76i-bdw7.geojson?$limit=200")
    feats = gj.get("features", [])
    if len(feats) != 78:
        raise SystemExit(f"precinct boundaries: {len(feats)} features, expected 78")
    for f in feats:
        f["properties"] = {"precinct": int(float(f["properties"]["precinct"]))}
    json.dump({"type": "FeatureCollection", "features": feats}, open(DATA / "precincts.geojson", "w"))
    print(f"Precincts: {len(feats)} boundaries -> data/precincts.geojson")
    return [(f["properties"]["precinct"], shape(f["geometry"]).buffer(0)) for f in feats]


def fetch_blocks():
    blocks = []
    for county in NYC_COUNTIES:
        q = {"where": f"STATE='36' AND COUNTY='{county}'", "outFields": "GEOID,POP100,AREALAND,INTPTLAT,INTPTLON",
             "returnGeometry": "false", "f": "json", "resultRecordCount": 100000}
        d = http(TIGER_BLOCKS + "?" + urllib.parse.urlencode(q))
        if d.get("exceededTransferLimit"):
            raise SystemExit(f"blocks {county}: hit the transfer limit")
        rows = [r["attributes"] for r in d.get("features", [])]
        if len(rows) < 1000:
            raise SystemExit(f"blocks {county}: only {len(rows)}")
        blocks += rows
    return blocks


def weights(precincts, blocks):
    """Share of each tract's 2020 residents living in each precinct, by block internal
    point. Tracts with no residents are split by land area instead."""
    tree = STRtree([g for _, g in precincts])
    codes = [p for p, _ in precincts]
    pop = defaultdict(Counter)
    land = defaultdict(Counter)
    unplaced_pop = 0
    for b in blocks:
        pt = Point(float(b["INTPTLON"]), float(b["INTPTLAT"]))
        idx = tree.query(pt, predicate="within")
        if len(idx):
            pct = codes[idx[0]]
        else:  # an internal point on a boundary or just offshore: the closest precinct
            pct = codes[tree.nearest(pt)]
            unplaced_pop += b["POP100"] or 0
        tract = b["GEOID"][:11]
        pop[tract][pct] += b["POP100"] or 0
        land[tract][pct] += b["AREALAND"] or 0
    out = {}
    for tract in set(pop) | set(land):
        src = pop[tract] if sum(pop[tract].values()) else land[tract]
        tot = sum(src.values())
        out[tract] = {str(p): round(v / tot, 6) for p, v in src.items() if v} if tot else {}
    total_pop = sum(b["POP100"] or 0 for b in blocks)
    split = sum(1 for w in out.values() if len(w) > 1 and max(w.values()) < 0.95)
    print(f"Blocks: {len(blocks):,} ({total_pop:,} residents); {unplaced_pop:,} residents in blocks placed by "
          f"nearest precinct; {split} of {len(out)} tracts have under 95% of residents in one precinct")
    return out, {"blocks": len(blocks), "residents_2020": total_pop, "residents_nearest": unplaced_pop,
                 "tracts_split_under_95pct": split, "tracts": len(out)}


def check_codes(counter, valid, label):
    bad = sum(n for (p, _), n in counter.items() if p not in valid)
    total = sum(counter.values())
    if bad > MAX_BAD_PRECINCT_SHARE * total:
        raise SystemExit(f"{label}: {bad} of {total} records have no valid precinct")
    return bad


def pct_code(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main():
    meta = json.load(open(DOCS / "youth_by_tract.json"))["meta"]
    precincts = fetch_precincts()
    valid = {p for p, _ in precincts}
    w, wrep = weights(precincts, fetch_blocks())
    json.dump(w, open(DATA / "tract_precinct_weights.json", "w"))

    counts = defaultdict(Counter)
    report = {"weights": wrep}

    # Arrests: the same calendar year, ages and NYPD jurisdictions as the neighborhood view.
    yr = meta["arrests"]["year"]
    window = f"arrest_date between '{yr}-01-01T00:00:00' and '{yr}-12-31T23:59:59'"
    rows = soql("8h9b-rp9u", f"select arrest_precinct, age_group, count(*) as n where {window} and "
                             f"age_group in('<18','18-24') and {NYPD_JURISDICTIONS} group by arrest_precinct, age_group limit 5000")
    arr = Counter()
    for r in rows:
        arr[(pct_code(r.get("arrest_precinct")), r["age_group"])] += int(r["n"])
    want = {"<18": meta["arrests"]["under18"], "18-24": meta["arrests"]["age18_24"]}
    got = {a: sum(n for (_, g), n in arr.items() if g == a) for a in want}
    if got != want:
        raise SystemExit(f"arrests by precinct {got} != neighborhood-view totals {want}")
    bad = check_codes(arr, valid, "arrests")
    for (p, a), n in arr.items():
        if p in valid:
            counts[p]["arrests_u18" if a == "<18" else "arrests_18_24"] += n
    report["arrests"] = {"year": yr, **got, "no_valid_precinct": bad}

    # Shootings: the same window; an incident's precinct is NYPD's precinct field.
    sh = meta["shootings"]
    start, end = sh["window_start"], sh["window_end"]
    inc = soql_all("5ucz-vwe8", "incident_key, precinct", f"occur_date between '{start}' and '{end}'", "incident_key")
    if len(inc) != sh["incidents"]:
        raise SystemExit(f"shootings: {len(inc)} incidents, the neighborhood view had {sh['incidents']}")
    pct_of = {r["incident_key"]: pct_code(r.get("precinct")) for r in inc}
    offenders = soql_all("gdk4-mbsv", "incident_key, perp_age_group", "1=1", "incident_key")
    victims = soql_all("pztn-9bne", "incident_key, victim_age_group", "1=1", "incident_key")
    susp = {o["incident_key"] for o in offenders if o["incident_key"] in pct_of and o.get("perp_age_group") in ("<18", "18-24")}
    vict = Counter(v["incident_key"] for v in victims if v["incident_key"] in pct_of and v.get("victim_age_group") in ("<18", "18-24"))
    if len(susp) != sh["incidents_with_suspect_under25"] or sum(vict.values()) != sh["victims_under25"]:
        raise SystemExit(f"shootings: {len(susp)} suspect incidents / {sum(vict.values())} victims do not match "
                         f"{sh['incidents_with_suspect_under25']} / {sh['victims_under25']}")
    sc = Counter()
    for k in susp:
        sc[(pct_of[k], "s")] += 1
    for k, n in vict.items():
        sc[(pct_of[k], "v")] += n
    bad = check_codes(sc, valid, "shootings")
    for (p, kind), n in sc.items():
        if p in valid:
            counts[p]["shoot_susp_u25" if kind == "s" else "shoot_vict_u25"] += n
    report["shootings"] = {"window_start": start, "window_end": end, "suspect_incidents": len(susp),
                           "victims": sum(vict.values()), "no_valid_precinct": bad}

    # Violent crimes against people under 25: the same 12 months by report date. The
    # historic file covers what it covers; the year-to-date file fills in after it.
    vv = meta["violent_victims"]
    v_start, v_end = vv["window_start"], vv["window_end"]
    hist_end = soql("qgea-i56i", "select max(rpt_dt) as mx")[0]["mx"][:10]
    codes = "ky_cd in(" + ",".join(f"'{c}'" for c in VIOLENT_NO_RAPE) + ")"
    vc = Counter()
    for ds, lo, hi in (("qgea-i56i", v_start, min(hist_end, v_end)), ("5uac-w243", None, v_end)):
        if ds == "5uac-w243":
            if hist_end >= v_end:
                continue
            lo = iso(dt.date.fromisoformat(hist_end) + dt.timedelta(days=1))
        rows = soql(ds, f"select addr_pct_cd, vic_age_group, count(*) as n where rpt_dt between '{lo}' and '{hi}' "
                        f"and {codes} and vic_age_group in('<18','18-24') group by addr_pct_cd, vic_age_group limit 5000")
        for r in rows:
            vc[(pct_code(r.get("addr_pct_cd")), r["vic_age_group"])] += int(r["n"])
    total_v = sum(vc.values())
    if total_v != vv["records"]:
        print(f"  note: {total_v:,} violent complaints by precinct vs {vv['records']:,} in the neighborhood pull "
              f"(the neighborhood pull counted only complaints with coordinates)")
        if abs(total_v - vv["records"]) > 0.01 * vv["records"]:
            raise SystemExit("violent complaints differ by more than 1% -- stopping")
    bad = check_codes(vc, valid, "violent complaints")
    for (p, a), n in vc.items():
        if p in valid:
            counts[p]["viol_vict_u18" if a == "<18" else "viol_vict_18_24"] += n
    report["violent_victims"] = {"window_start": v_start, "window_end": v_end, "records": total_v,
                                 "historic_through": min(hist_end, v_end), "no_valid_precinct": bad}

    out = {"meta": report, "precincts": {str(p): dict(counts[p]) for p in sorted(valid)}}
    json.dump(out, open(DATA / "precinct_counts.json", "w"), indent=1)
    print(f"Arrests {got}; shootings {len(susp)} suspect incidents, {sum(vict.values())} victims; "
          f"violent complaints {total_v:,} -> data/precinct_counts.json")


if __name__ == "__main__":
    main()
