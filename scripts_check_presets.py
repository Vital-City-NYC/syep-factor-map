"""Check that every place named in a preset story sits in the cell the story says.
Run after build.py: python3 scripts_check_presets.py"""
import json, math
F = [f["properties"] for f in json.load(open("docs/ntas.geojson"))["features"]]
def q(s, t):
    i = (len(s) - 1) * t; lo, hi = math.floor(i), math.ceil(i); return s[lo] + (s[hi] - s[lo]) * (i - lo)
def cells(x, y):
    P = [p for p in F if p.get(x) is not None and p.get(y) is not None]
    xs, ys = sorted(p[x] for p in P), sorted(p[y] for p in P)
    x1, x2, y1, y2 = q(xs, 1/3), q(xs, 2/3), q(ys, 1/3), q(ys, 2/3)
    b = lambda v, a, c: 0 if v <= a else 1 if v <= c else 2
    return {p["nta"]: (b(p[x], x1, x2), b(p[y], y1, y2)) for p in P}
UL, UR, LR, MR = (0, 2), (2, 2), (2, 0), (2, 1)
CLAIMS = [
    ("syep_sites_1mi", "pov_rate_16_24", UL, ["Borough Park", "Kensington", "Gravesend (South)", "Brighton Beach",
        "Rockaway Beach-Arverne-Edgemere", "Rosebank-Shore Acres-Park Hill", "Upper East Side-Lenox Hill-Roosevelt Island"]),
    ("syep_sites_1mi", "pov_rate_16_24", UR, ["Harlem (North)", "Bedford-Stuyvesant (East)", "Bushwick (East)"]),
    ("syep_sites_1mi", "neet_16_19", UL, ["South Ozone Park", "South Jamaica", "Queens Village", "East Elmhurst",
        "Rockaway Beach-Arverne-Edgemere", "Bay Ridge", "Dyker Heights", "Wakefield-Woodlawn", "Great Kills-Eltingville"]),
    ("syep_sites_1mi", "shoot_vict_u25_rate", UL, ["Jamaica", "South Jamaica", "Laurelton", "Springfield Gardens (South)-Brookville",
        "Rockaway Beach-Arverne-Edgemere", "Coney Island-Sea Gate", "Sheepshead Bay-Manhattan Beach-Gerritsen Beach", "Eastchester-Edenwald-Baychester", "Wakefield-Woodlawn"]),
    ("syep_sites_1mi", "viol_vict_u25_rate", UL, ["Jamaica", "South Jamaica", "Coney Island-Sea Gate", "Brighton Beach",
        "Eastchester-Edenwald-Baychester", "Wakefield-Woodlawn", "Westchester Square"]),
    ("syep_sites_1mi", "pop_16_24", UL, ["Borough Park", "Bay Ridge", "Flatlands", "South Ozone Park", "Jamaica", "Queens Village",
        "Eastchester-Edenwald-Baychester", "Great Kills-Eltingville"]),
    ("unemp_16_24", "shoot_vict_u25_rate", UR, ["Mott Haven-Port Morris", "Concourse-Concourse Village", "Williamsbridge-Olinville",
        "Bedford-Stuyvesant (West)", "Crown Heights (North)", "Harlem (North)", "Jamaica"]),
    ("unemp_16_24", "shoot_vict_u25_rate", LR, ["Kew Gardens", "Whitestone-Beechhurst", "Astoria (Central)", "New Dorp-Midland Beach"]),
    ("pov_rate_u18", "arrests_u18_rate", (0, 2), ["Greenwich Village", "Gramercy", "Murray Hill-Kips Bay", "Hell's Kitchen"]),
    ("pov_rate_u18", "arrests_u18_rate", LR, ["Borough Park", "Bensonhurst", "South Williamsburg", "Washington Heights (South)"]),
]
bad = 0
for x, y, cell, names in CLAIMS:
    c = cells(x, y)
    for n in names:
        if n not in c:
            print("NAME NOT FOUND", n); bad += 1
        elif c[n] != cell:
            print(f"WRONG CELL {x} x {y}: {n} is {c[n]}, story says {cell}"); bad += 1
# Sunset Park appears as a group in preset 7; at least one Sunset Park NTA must be in the lower-right
c = cells("pov_rate_u18", "arrests_u18_rate")
if not any(c.get(n) == LR for n in c if n.startswith("Sunset Park")):
    print("WRONG: no Sunset Park NTA in lower-right for child poverty x arrests"); bad += 1
print("preset claims OK" if not bad else f"{bad} problems")

# Borough wording: "led by" = largest borough in the cell; "mostly" = majority of the cell.
from collections import Counter
BORO = {p["nta"]: p["borough"] for p in F}
WORDING = [
    ("syep_sites_1mi", "pov_rate_16_24", UR, "led", ["Bronx"]),
    ("syep_sites_1mi", "neet_16_19", UR, "led", ["Bronx"]),
    ("syep_sites_1mi", "neet_16_19", UL, "mostly", ["Queens", "Brooklyn"]),
    ("syep_sites_1mi", "viol_vict_u25_rate", UR, "led", ["Bronx"]),
    ("unemp_16_24", "shoot_vict_u25_rate", UR, "mostly", ["Bronx", "Brooklyn"]),
    ("unemp_16_24", "shoot_vict_u25_rate", LR, "mostly", ["Queens", "Staten Island"]),
    ("pov_rate_u18", "arrests_u18_rate", (0, 2), "mostly", ["Manhattan"]),
]
for x, y, cell, kind, boros in WORDING:
    c = cells(x, y)
    cnt = Counter(BORO[n] for n, v in c.items() if v == cell)
    tot = sum(cnt.values())
    ok = (cnt.most_common(1)[0][0] == boros[0]) if kind == "led" else (sum(cnt[b] for b in boros) > tot / 2)
    print(("ok   " if ok else "FAIL ") + f"{x} x {y} {cell} {kind} {boros}: {dict(cnt)}")

# ---- Precinct stories (storyPct), added Oct. 9, 2026 ----
# These name whole cells, so each check asserts the exact set of precincts in the cell,
# the borough counts the story gives and the correlation it quotes.
PF = [f["properties"] for f in json.load(open("docs/precincts.geojson"))["features"]]
def pcells(x, y):
    P = [p for p in PF if p.get(x) is not None and p.get(y) is not None]
    xs, ys = sorted(p[x] for p in P), sorted(p[y] for p in P)
    x1, x2, y1, y2 = q(xs, 1/3), q(xs, 2/3), q(ys, 1/3), q(ys, 2/3)
    b = lambda v, a, c: 0 if v <= a else 1 if v <= c else 2
    return {p["precinct"]: (b(p[x], x1, x2), b(p[y], y1, y2)) for p in P}
def pcorr(x, y):
    P = [p for p in PF if p.get(x) is not None and p.get(y) is not None]
    a, c = [p[x] for p in P], [p[y] for p in P]
    ma, mc = sum(a) / len(a), sum(c) / len(c)
    return sum((u - ma) * (v - mc) for u, v in zip(a, c)) / math.sqrt(sum((u - ma) ** 2 for u in a) * sum((v - mc) ** 2 for v in c))
PB = {p["precinct"]: p["borough"] for p in PF}
S = "syep_sites_1mi"
PCT_CLAIMS = [  # (x, y, cell, exact precincts or None, borough counts or None)
    (S, "pov_rate_16_24", UL, {66}, None),
    (S, "pov_rate_16_24", UR, None, {"Manhattan": 9, "Bronx": 7, "Brooklyn": 3}),
    (S, "neet_16_19", UL, {47, 68, 103, 115}, None),
    (S, "neet_16_19", UR, None, {"Bronx": 6, "Manhattan": 5}),
    (S, "shoot_vict_u25_rate", UL, {47, 60, 103}, None),
    (S, "shoot_vict_u25_rate", UR, None, {"Bronx": 6}),
    (S, "viol_vict_u25_rate", UL, {47, 60, 103}, None),
    (S, "viol_vict_u25_rate", UR, None, {"Bronx": 7, "Manhattan": 6}),
    (S, "pop_16_24", UL, {102, 106, 107, 109, 115, 62, 66, 121, 122, 47}, None),
    ("unemp_16_24", "shoot_vict_u25_rate", UR, None, {"Bronx": 6, "Manhattan": 4, "Brooklyn": 4, "Queens": 2}),
    ("unemp_16_24", "shoot_vict_u25_rate", LR, {62, 63, 106}, None),
    ("pov_rate_u18", "arrests_u18_rate", (0, 2), {6, 13, 14, 18, 28, 76, 84}, None),
    ("pov_rate_u18", "arrests_u18_rate", LR, {33, 66, 90}, None),
]
pbad = 0
for x, y, cell, exact, boros in PCT_CLAIMS:
    c = pcells(x, y)
    members = {p for p, v in c.items() if v == cell}
    if exact is not None and members != exact:
        print(f"PRECINCT CELL {x} x {y} {cell}: data {sorted(members)}, story {sorted(exact)}"); pbad += 1
    if boros is not None:
        cnt = Counter(PB[p] for p in members)
        for b, n in boros.items():
            if cnt[b] != n:
                print(f"PRECINCT BOROUGHS {x} x {y} {cell}: {b} {cnt[b]}, story {n}"); pbad += 1
        top = cnt.most_common(1)[0][0]
        if top != next(iter(boros)):
            print(f"PRECINCT 'led by' {x} x {y}: largest is {top}"); pbad += 1
    # 26th and 9th sit with the high-poverty, many-sites precincts
for p in (26, 9):
    if pcells(S, "pov_rate_16_24")[p] != UR:
        print(f"PRECINCT {p} not upper-right for youth poverty x sites"); pbad += 1
for x, y, r in ((S, "pov_rate_16_24", 0.69), (S, "neet_16_19", 0.30), (S, "shoot_vict_u25_rate", 0.44),
                ("unemp_16_24", "shoot_vict_u25_rate", 0.50), ("pov_rate_u18", "arrests_u18_rate", -0.11)):
    if round(pcorr(x, y), 2) != r:
        print(f"PRECINCT correlation {x} x {y}: {pcorr(x, y):.3f}, story {r}"); pbad += 1
print("precinct preset claims OK" if not pbad else f"{pbad} precinct problems")
