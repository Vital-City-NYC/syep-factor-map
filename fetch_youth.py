#!/usr/bin/env python3
"""Fetch the youth-specific data for the summer jobs factor map and place it in tracts.

Sources (all free; the Census API needs a key in CENSUS_API_KEY):
  NYPD station houses   78 precinct pages on nyc.gov, geocoded with NYC Planning GeoSearch
  NYPD Arrests          8h9b-rp9u (Historic). Calendar ARREST_YEAR, age groups <18 and 18-24.
                        2025 cannot be used: the file has no age on 53% of 2025 arrests.
  NYPD Arrests YTD      uip8-fykc. January-June 2026, used only to check that the 2024
                        geography still holds (methodology), not shown on the map.
  NYPD Shootings        5ucz-vwe8 incidents + gdk4-mbsv offenders + pztn-9bne victims.
                        The three most recent years in the file.
  NYPD Complaints       qgea-i56i (Historic) + 5uac-w243 (YTD). Murder, robbery and
                        felony assault with a victim under 25, rolling 12 months by
                        report date (RPT_DT). Rape is left out: NYPD places 94% of rape
                        complaints at the station house to protect victims.
  DYCD Program Sites    ebkm-iyma. Summer Youth Employment Program sites, latest fiscal
                        year, year-round program types dropped, one entry per location.
  2020 Census DHC       PCT12 single years of age: residents 14-24 by tract (SYEP's
                        eligibility range).
  Census Gazetteer      2020 tract internal points, for the one-mile site counts.

Every pull checks itself and stops rather than write an empty or implausible file.
Outputs: data/stations.json, docs/youth_by_tract.json, docs/syep_sites.json
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from nypd_places import TractIndex, load_stations, meters, place, STATION_RADIUS_M

ROOT = Path(__file__).parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

ARREST_YEAR = 2024
MAX_MISSING_AGE_SHARE = 0.02
# NYPD's own jurisdictions (0 patrol, 1 transit, 2 housing, per the dataset's column
# description). Codes 3 and up are other agencies; they include a cluster of arrests at
# one Hunts Point point under code 72 (mostly offenses against public administration
# and felony assault) that would otherwise dominate that neighborhood.
NYPD_JURISDICTIONS = "jurisdiction_code in('0','1','2')"
SHOOTING_YEARS = 3
VIOLENT_NO_RAPE = ("101", "105", "106")          # murder, robbery, felony assault
MAX_UNKNOWN_VICTIM_AGE = 0.08
YEAR_ROUND_TYPES = {"WLG (OY)", "WLG (YY)", "Year Round Sector Focus"}
SITE_RADIUS_MILES = 1.0
NYC_COUNTIES = ("005", "047", "061", "081", "085")

# The 40th Precinct moved from 257 Alexander Avenue to 567 East 149th Street in late
# 2024. NYPD still places 40th Precinct records at the old building (2024 arrests and
# 2026 rape complaints both stack there), so both addresses count as its station house.
FORMER_STATIONS = [{
    "precinct": 40, "address": "257 Alexander Avenue", "zip": "10454",
    "note": "40th Precinct station house until late 2024",
    "sources": ["https://www.nyc.gov/assets/bronxcb4/downloads/pdf/2022/NYPD_in_Bronx_Community_Board_Four.pdf",
                "https://www.bxtimes.com/?p=142587"],
}]
NAMED_PRECINCTS = {"midtown-north": 18, "midtown-south": 14, "central-park": 22}
ADDRESS_FIXES = {  # address text on the NYPD page -> something GeoSearch can parse
    "2575 Coney Island": "2575 Coney Island Avenue",
    "64-2 Catalpa Ave.": "64-02 Catalpa Avenue",
    "2271-89 8th Ave.": "2271 8th Avenue",
    "86th and Transverse Rd": "86 Street Transverse",
}


def http(url, raw=False, tries=5):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read().decode("utf-8", "ignore")
            return body if raw else json.loads(body)
        except Exception as e:  # transient network errors and Socrata throttling (403/429)
            err = e
            time.sleep(3 * (i + 1))
    raise SystemExit(f"FAILED after {tries} tries: {url}\n  {err}")


def soql(dataset, query):
    url = f"https://data.cityofnewyork.us/resource/{dataset}.json?" + urllib.parse.urlencode({"$query": query})
    return http(url)


def soql_all(dataset, select, where, order, page=50000):
    out, offset = [], 0
    while True:
        rows = soql(dataset, f"select {select} where {where} order by {order} limit {page} offset {offset}")
        out.extend(rows)
        if len(rows) < page:
            return out
        offset += page


def iso(d):
    return d.strftime("%Y-%m-%dT00:00:00")


# ---------------------------------------------------------------- station houses
def geocode(text):
    url = "https://geosearch.planninglabs.nyc/v2/search?" + urllib.parse.urlencode({"text": text, "size": 1})
    feats = http(url)["features"]
    if not feats:
        raise SystemExit(f"GeoSearch found nothing for {text!r}")
    f = feats[0]
    return f["properties"]["label"], f["properties"].get("postalcode"), f["geometry"]["coordinates"]


def build_stations():
    landing = http("https://www.nyc.gov/site/nypd/bureaus/patrol/precincts-landing.page", raw=True)
    links = sorted(set(re.findall(r'href="(/site/nypd/bureaus/patrol/precincts/[^"]+\.page)"', landing)))
    if len(links) != 78:
        raise SystemExit(f"NYPD precinct list has {len(links)} pages, expected 78")
    out = []
    for href in links:
        url = "https://www.nyc.gov" + href
        page = re.sub(r"<script.*?</script>", "", http(url, raw=True), flags=re.S)
        lines = [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", page)).split("\n") if l.strip()]
        idx = [i for i, l in enumerate(lines) if l == "Contact Information"]
        if not idx:
            raise SystemExit(f"no contact block on {url}")
        addr = lines[idx[-1] + 1].rstrip(",").strip()
        cityline = lines[idx[-1] + 2]
        zipc = (re.search(r"\b(1\d{4})\b", cityline) or [None, ""])[1]
        slug = href.split("/")[-1].replace("-precinct.page", "")
        pct = NAMED_PRECINCTS.get(slug) or int(re.match(r"\d+", slug).group())
        label, postal, (lon, lat) = geocode(f"{ADDRESS_FIXES.get(addr, addr)} {zipc}")
        if zipc and postal and postal != zipc:
            raise SystemExit(f"precinct {pct}: geocoded ZIP {postal} != page ZIP {zipc} ({addr})")
        out.append({"precinct": pct, "address": addr, "zip": zipc, "page": url,
                    "geocoded": label, "lat": lat, "lon": lon})
        time.sleep(0.25)
    for s in FORMER_STATIONS:
        label, postal, (lon, lat) = geocode(f"{s['address']} {s['zip']}")
        out.append({"precinct": s["precinct"], "address": s["address"], "zip": s["zip"],
                    "page": s["sources"][0], "sources": s["sources"], "note": s["note"],
                    "geocoded": label, "lat": lat, "lon": lon})
    if len({s["precinct"] for s in out}) != 78:
        raise SystemExit("station list does not cover 78 precincts")
    DATA.mkdir(exist_ok=True)
    json.dump(sorted(out, key=lambda s: (s["precinct"], s["address"])), open(DATA / "stations.json", "w"), indent=1)
    print(f"Stations: {len(out)} station-house points for 78 precincts -> data/stations.json")


def check_stations(stations):
    """The test behind STATION_RADIUS_M: in each precinct, the arrest point with the
    most 2024 arrests across many offense types should be a station house."""
    rows = soql("8h9b-rp9u", (
        "select arrest_precinct, latitude, longitude, count(*) as n, count(distinct ofns_desc) as k "
        f"where arrest_date between '{ARREST_YEAR}-01-01T00:00:00' and '{ARREST_YEAR}-12-31T23:59:59' "
        "group by arrest_precinct, latitude, longitude having count(*) >= 40 limit 10000"))
    best = {}
    for r in rows:
        if int(r["k"]) < 15:      # a store or mall stacks shoplifting; a station stacks everything
            continue
        p = int(r["arrest_precinct"])
        if p not in best or int(r["n"]) > int(best[p]["n"]):
            best[p] = r
    misses = []
    for p, r in best.items():
        d = min(meters(float(r["latitude"]), float(r["longitude"]), a, b) for a, b in stations.get(p, []))
        if d > STATION_RADIUS_M:
            misses.append((p, r["n"], round(d)))
    print(f"Station check: {len(best) - len(misses)} of {len(best)} precincts' busiest mixed-offense "
          f"arrest point lies within {STATION_RADIUS_M} m of a station house; misses: {misses}")
    if len(misses) > 2:
        raise SystemExit("station-house check failed -- review data/stations.json")
    return {"precincts_checked": len(best), "misses": misses}


# ---------------------------------------------------------------- pulls
def latlon(r, lat="latitude", lon="longitude"):
    try:
        a, b = float(r[lat]), float(r[lon])
    except (KeyError, TypeError, ValueError):
        return None
    if a < 0 < b:          # some NYPD shooting rows have the two columns swapped
        a, b = b, a
    if not (40.45 <= a <= 40.95 and -74.30 <= b <= -73.65):
        return None
    return a, b


def fetch_arrests(stations, index):
    yr = ARREST_YEAR
    window = f"arrest_date between '{yr}-01-01T00:00:00' and '{yr}-12-31T23:59:59'"
    groups = soql("8h9b-rp9u", f"select age_group, count(*) as n where {window} group by age_group")
    total = sum(int(g["n"]) for g in groups)
    known = {"<18", "18-24", "25-44", "45-64", "65+"}
    missing = sum(int(g["n"]) for g in groups if g.get("age_group") not in known)
    if total < 100_000 or missing / total > MAX_MISSING_AGE_SHARE:
        raise SystemExit(f"Arrests {yr}: {missing:,} of {total:,} lack an age group -- stopping")
    rows = soql_all("8h9b-rp9u", "arrest_key, age_group, arrest_precinct, latitude, longitude",
                    f"{window} and age_group in('<18','18-24') and {NYPD_JURISDICTIONS}", "arrest_key")
    nypd = soql("8h9b-rp9u", f"select age_group, count(*) as n where {window} and {NYPD_JURISDICTIONS} "
                             "and age_group in('<18','18-24') group by age_group")
    expect = {g["age_group"]: int(g["n"]) for g in nypd}
    all_youth = {g["age_group"]: int(g["n"]) for g in groups if g.get("age_group") in ("<18", "18-24")}
    got = Counter(r["age_group"] for r in rows)
    if dict(got) != expect:
        raise SystemExit(f"Arrests: pulled {dict(got)} but counts say {expect}")
    recs, bad = [], 0
    for r in rows:
        ll = latlon(r)
        if not ll:
            bad += 1
            continue
        recs.append({"lat": ll[0], "lon": ll[1], "pct": r.get("arrest_precinct"), "age": r["age_group"]})
    counts, rep = place(recs, stations, index, "pct", "age")
    rep.update({"year": yr, "all_arrests": total, "missing_age": missing,
                "under18": expect["<18"], "age18_24": expect["18-24"], "no_coordinates": bad,
                "other_agency_excluded": {k: all_youth[k] - expect[k] for k in expect}})
    print(f"Arrests {yr}: {expect['<18']:,} under 18, {expect['18-24']:,} aged 18-24 in NYPD jurisdictions "
          f"(other agencies excluded: {rep['other_agency_excluded']}); "
          f"{rep['left_out_at_station_house']:,} at a station house left out {rep['left_out_by_category']}, "
          f"{rep['shared_on_boundary']:,} shared on a boundary, {rep['unplaced']} unplaced, {bad} without coordinates")
    return counts, rep


def fetch_arrests_2026(stations, index):
    """Stability check only: Jan-Jun 2026 youth arrests, placed the same way."""
    groups = soql("uip8-fykc", "select age_group, count(*) as n, max(arrest_date) as mx group by age_group")
    end = max(g["mx"] for g in groups)[:10]
    rows = soql_all("uip8-fykc", "arrest_key, age_group, arrest_precinct, latitude, longitude",
                    f"age_group in('<18','18-24') and {NYPD_JURISDICTIONS}", "arrest_key")
    recs = []
    for r in rows:
        ll = latlon(r)
        if ll:
            recs.append({"lat": ll[0], "lon": ll[1], "pct": r.get("arrest_precinct"), "age": r["age_group"]})
    counts, rep = place(recs, stations, index, "pct", "age")
    rep["through"] = end
    print(f"Arrests 2026 YTD (check only): {len(recs):,} youth arrests through {end}")
    return counts, rep


def fetch_shootings(stations, index):
    mx = soql("5ucz-vwe8", "select max(occur_date) as mx")[0]["mx"][:10]
    end = dt.date.fromisoformat(mx)
    start = dt.date(end.year - SHOOTING_YEARS, end.month, end.day) + dt.timedelta(days=1)
    inc = soql_all("5ucz-vwe8", "incident_key, occur_date, precinct, latitude, longitude, x_coord_cd, y_coord_cd",
                   f"occur_date between '{iso(start)}' and '{iso(end)}'", "incident_key")
    n_expect = int(soql("5ucz-vwe8", f"select count(*) as n where occur_date between '{iso(start)}' and '{iso(end)}'")[0]["n"])
    if len(inc) != n_expect or n_expect < 1000:
        raise SystemExit(f"Shootings: pulled {len(inc)}, expected {n_expect}")
    keys = {r["incident_key"] for r in inc}
    offenders = [o for o in soql_all("gdk4-mbsv", "incident_key, perp_id, perp_age_group", "1=1", "perp_id")
                 if o["incident_key"] in keys]
    victims = [v for v in soql_all("pztn-9bne", "incident_key, victim_id, victim_age_group", "1=1", "victim_id")
               if v["incident_key"] in keys]
    susp_u25 = {o["incident_key"] for o in offenders if o.get("perp_age_group") in ("<18", "18-24")}
    vict_u25 = Counter(v["incident_key"] for v in victims if v.get("victim_age_group") in ("<18", "18-24"))
    with_offender = {o["incident_key"] for o in offenders}
    # Two coordinate sets per incident: latitude/longitude (columns swapped on most
    # rows) and NYPD's New York State Plane x/y. Use latitude/longitude when it is a
    # valid New York City point, else the state-plane point; report disagreements.
    swapped = sum(1 for r in inc if r.get("latitude") and float(r["latitude"]) < 0)
    try:
        from pyproj import Transformer
        tf = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)
    except ImportError:
        tf = None
    def state_plane(r):
        try:
            lon, lat = tf.transform(float(r["x_coord_cd"]), float(r["y_coord_cd"]))
        except (TypeError, ValueError, KeyError):
            return None
        return (lat, lon) if (40.45 <= lat <= 40.95 and -74.30 <= lon <= -73.65) else None
    disagree, from_sp = [], 0
    for r in inc:
        ll, sp = latlon(r), (state_plane(r) if tf else None)
        if ll and sp and meters(ll[0], ll[1], sp[0], sp[1]) > 100:
            disagree.append(r["incident_key"])
        if not ll and sp:
            r["latitude"], r["longitude"] = sp
            from_sp += 1
    if len(disagree) > 0.005 * len(inc):
        raise SystemExit(f"Shootings: {len(disagree)} incidents' two coordinate sets disagree -- stopping")
    check = {"disagreeing_over_100m": disagree, "located_from_state_plane": from_sp,
             "state_plane_checked": tf is not None}
    recs_s, recs_v, bad = [], [], 0
    for r in inc:
        ll = latlon(r)
        if not ll:
            bad += 1
            continue
        if r["incident_key"] in susp_u25:
            recs_s.append({"lat": ll[0], "lon": ll[1], "pct": r.get("precinct"), "cat": "susp_u25"})
        for _ in range(vict_u25.get(r["incident_key"], 0)):
            recs_v.append({"lat": ll[0], "lon": ll[1], "pct": r.get("precinct"), "cat": "vict_u25"})
    cs, rep_s = place(recs_s, stations, index, "pct", "cat")
    cv, rep_v = place(recs_v, stations, index, "pct", "cat")
    rep = {"window_start": start.isoformat(), "window_end": end.isoformat(), "incidents": len(inc),
           "incidents_with_offender_record": len(with_offender), "incidents_with_suspect_under25": len(susp_u25),
           "victims_under25": sum(vict_u25.values()), "swapped_coordinates_fixed": swapped,
           "coordinate_check": check, "no_coordinates": bad,
           "suspect_placement": rep_s, "victim_placement": rep_v}
    print(f"Shootings {start}..{end}: {len(inc):,} incidents, {len(susp_u25):,} with a suspect under 25, "
          f"{sum(vict_u25.values()):,} victims under 25; {swapped} swapped coordinate pairs fixed; "
          f"state-plane check {check}")
    return cs, cv, rep


def fetch_violent_victims(stations, index):
    end_h = soql("qgea-i56i", "select max(rpt_dt) as mx")[0]["mx"][:10]
    end_c = soql("5uac-w243", "select max(rpt_dt) as mx")[0]["mx"][:10]
    end = max(dt.date.fromisoformat(end_h), dt.date.fromisoformat(end_c))
    start = end - dt.timedelta(days=364)
    codes = "ky_cd in(" + ",".join(f"'{c}'" for c in VIOLENT_NO_RAPE) + ")"
    recs, total, known = [], 0, 0
    for ds, lo, hi in (("qgea-i56i", start, dt.date.fromisoformat(end_h)),
                       ("5uac-w243", dt.date.fromisoformat(end_h) + dt.timedelta(days=1), end)):
        if lo > hi:
            continue
        where = f"rpt_dt between '{iso(lo)}' and '{iso(hi)}' and {codes}"
        g = soql(ds, f"select vic_age_group, count(*) as n where {where} group by vic_age_group")
        total += sum(int(x["n"]) for x in g)
        known += sum(int(x["n"]) for x in g if x.get("vic_age_group") in ("<18", "18-24", "25-44", "45-64", "65+"))
        rows = soql_all(ds, "cmplnt_num, vic_age_group, addr_pct_cd, latitude, longitude",
                        f"{where} and vic_age_group in('<18','18-24')", "cmplnt_num")
        for r in rows:
            ll = latlon(r)
            if ll:
                recs.append({"lat": ll[0], "lon": ll[1], "pct": r.get("addr_pct_cd"), "age": r["vic_age_group"]})
    if total < 20000 or 1 - known / total > MAX_UNKNOWN_VICTIM_AGE:
        raise SystemExit(f"Violent complaints: {total - known:,} of {total:,} lack a victim age -- stopping")
    counts, rep = place(recs, stations, index, "pct", "age")
    rep.update({"window_start": start.isoformat(), "window_end": end.isoformat(),
                "complaints_all_ages": total, "victim_age_unknown": total - known})
    print(f"Violent felonies {start}..{end} (no rape): {len(recs):,} with a victim under 25 of {total:,}; "
          f"{total - known:,} without a victim age")
    return counts, rep


def fetch_syep_sites(index):
    rows = soql_all("ebkm-iyma", "date, provider, program_site_name, program_type, street_address, borough, latitude, longitude",
                    "program_area='SYEP' and latitude IS NOT NULL", "date, program_site_name")
    fy = max(int(r["date"]) for r in rows if r.get("date"))
    def summer_locs(year):
        locs = {}
        for r in rows:
            if r.get("date") and int(r["date"]) == year and r.get("program_type") not in YEAR_ROUND_TYPES:
                k = (round(float(r["latitude"]), 6), round(float(r["longitude"]), 6))
                s = locs.setdefault(k, {"lat": k[0], "lon": k[1], "names": set(), "providers": set(),
                                        "address": r.get("street_address") or ""})
                s["names"].add((r.get("program_site_name") or "").strip())
                s["providers"].add((r.get("provider") or "").strip())
        return locs
    sites, prev = summer_locs(fy), summer_locs(fy - 1)
    if len(sites) < 60 or (prev and len(sites) < 0.7 * len(prev)):
        raise SystemExit(f"SYEP: {len(sites)} sites in fiscal {fy} vs {len(prev)} the year before -- stopping")
    out = []
    for s in sites.values():
        g = index.tract_of(s["lat"], s["lon"])
        out.append({"lat": s["lat"], "lon": s["lon"], "tract": g, "address": s["address"].title(),
                    "names": sorted(n for n in s["names"] if n), "providers": sorted(p for p in s["providers"] if p)})
    print(f"SYEP: {len(out)} summer sites in fiscal {fy} (summer {fy - 1}); {sum(1 for s in out if not s['tract'])} outside every tract")
    return out, {"fiscal_year": fy, "summer": fy - 1, "sites": len(out), "sites_prior_year": len(prev)}


def fetch_census_14_24():
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        raise SystemExit("set CENSUS_API_KEY (the Census API rejects keyless requests)")
    male = [f"PCT12_{i:03d}N" for i in range(17, 28)]      # 14 through 24 years
    female = [f"PCT12_{i:03d}N" for i in range(121, 132)]
    meta = http(f"https://api.census.gov/data/2020/dec/dhc/groups/PCT12.json?key={key}")["variables"]
    for i, code in enumerate(male + female):
        age = 14 + (i % 11)
        want = f"!!{age} years"
        if not meta[code]["label"].endswith(want) or ("Male" in meta[code]["label"]) != (i < 11):
            raise SystemExit(f"PCT12 label check failed for {code}: {meta[code]['label']}")
    out = {}
    for c in NYC_COUNTIES:
        url = (f"https://api.census.gov/data/2020/dec/dhc?get={','.join(male + female)}"
               f"&for=tract:*&in=state:36%20county:{c}&key={key}")
        hdr, *body = http(url)
        for row in body:
            rec = dict(zip(hdr, row))
            out["36" + c + rec["tract"]] = sum(int(rec[k]) for k in male + female)
    if len(out) < 2300:
        raise SystemExit(f"PCT12: only {len(out)} tracts")
    print(f"2020 census: {sum(out.values()):,} residents aged 14-24 in {len(out):,} tracts")
    return out


def fetch_tract_points():
    txt = http("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_gaz_tracts_36.txt", raw=True)
    pts = {}
    for line in txt.splitlines()[1:]:
        f = [x.strip() for x in line.split("\t")]
        if len(f) >= 8 and f[1][2:5] in NYC_COUNTIES:
            pts[f[1]] = (float(f[6]), float(f[7]))
    if len(pts) < 2300:
        raise SystemExit(f"Gazetteer: only {len(pts)} NYC tracts")
    return pts


def main():
    if "--stations" in sys.argv or not (DATA / "stations.json").exists():
        build_stations()
    stations = load_stations()
    station_check = check_stations(stations)
    index = TractIndex()

    arrests, arr_rep = fetch_arrests(stations, index)
    arrests26, arr26_rep = fetch_arrests_2026(stations, index)
    s_susp, s_vict, sh_rep = fetch_shootings(stations, index)
    victims, vic_rep = fetch_violent_victims(stations, index)
    sites, site_rep = fetch_syep_sites(index)
    pop1424 = fetch_census_14_24()
    pts = fetch_tract_points()

    radius_m = SITE_RADIUS_MILES * 1609.344
    tracts = {}
    for g in index.geoids:
        rec = {
            "arrests_u18": arrests.get(g, {}).get("<18", 0.0),
            "arrests_18_24": arrests.get(g, {}).get("18-24", 0.0),
            "arrests26_u18": arrests26.get(g, {}).get("<18", 0.0),
            "arrests26_18_24": arrests26.get(g, {}).get("18-24", 0.0),
            "shoot_susp_u25": s_susp.get(g, {}).get("susp_u25", 0.0),
            "shoot_vict_u25": s_vict.get(g, {}).get("vict_u25", 0.0),
            "viol_vict_u18": victims.get(g, {}).get("<18", 0.0),
            "viol_vict_18_24": victims.get(g, {}).get("18-24", 0.0),
            "syep_sites_in": sum(1 for s in sites if s["tract"] == g),
            "syep_age_2020": pop1424.get(g),
        }
        if g in pts:
            lat, lon = pts[g]
            rec["syep_sites_1mi"] = sum(1 for s in sites if meters(lat, lon, s["lat"], s["lon"]) <= radius_m)
        tracts[g] = rec
    meta = {"pulled": dt.date.today().isoformat(), "station_radius_m": STATION_RADIUS_M,
            "station_check": station_check, "arrests": arr_rep, "arrests_2026_check": arr26_rep,
            "shootings": sh_rep, "violent_victims": vic_rep, "syep": site_rep,
            "site_radius_miles": SITE_RADIUS_MILES}
    json.dump({"meta": meta, "tracts": tracts}, open(DOCS / "youth_by_tract.json", "w"))
    json.dump({"meta": site_rep, "sites": sites}, open(DOCS / "syep_sites.json", "w"))
    print("Wrote docs/youth_by_tract.json and docs/syep_sites.json")


if __name__ == "__main__":
    main()
