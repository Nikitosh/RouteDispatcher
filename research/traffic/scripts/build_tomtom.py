"""Build tomtom_moscow_hourly.json from archived TomTom Traffic Index files."""
import datetime as dt
import json
import sys
from collections import defaultdict

RAW = sys.argv[1]
OUT = sys.argv[2]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = DAYS[:5]

pd = json.load(open(f"{RAW}/tomtom_pagedata_en_gb_20220214.json"))
city = pd["result"]["data"]["citiesJson"]

years = {}
for y in city["availableYears"]:
    wh = city[f"stats{y}"]["results"]["weekHours"]
    cong = {d: [round(float(x["congestion"]), 1) for x in wh[d]] for d in DAYS}
    wk = [round(sum(cong[d][h] for d in WEEKDAYS) / 5, 1) for h in range(24)]
    cong["weekday_mean"] = wk
    years[str(y)] = {
        "congestion_pct": cong,
        "factor": {d: [round(1 + c / 100, 3) for c in v] for d, v in cong.items()},
        "annual_congestion_pct": city[f"stats{y}"]["congestion"],
        "highway_congestion_pct": round(city[f"stats{y}"]["results"]["allDays"]["highway"]["congestion"], 1),
        "nonhighway_congestion_pct": round(city[f"stats{y}"]["results"]["allDays"]["nonHighway"]["congestion"], 1),
    }

# 2023 cross-check: liveHourly snapshot of 2023-12-03 holds TravelTimeHistoric (typical
# travel time of TomTom's reference trip set) for the past 168 hours. A record stamped
# T covers the hour ending at round(T); so hour = round(T) - 1 (MSK).
lh = json.load(open(f"{RAW}/tomtom_liveHourly_20231203.json"))["data"]
msk = dt.timezone(dt.timedelta(hours=3))
tth = defaultdict(dict)
for r in lh:
    t = dt.datetime.fromtimestamp(r["UpdateTime"] / 1000, msk)
    t = (t + dt.timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    tth[DAYS[t.weekday()]][t.hour] = r["TravelTimeHistoric"]
wd_vals = [v for d in WEEKDAYS for v in tth[d].values()]
ff = min(wd_vals)
prof2023 = {}
for d in WEEKDAYS:
    prof2023[d] = [round((tth[d][h] / ff - 1) * 100, 1) if h in tth[d] else None for h in range(24)]
prof2023["weekday_mean"] = [
    round(sum(prof2023[d][h] for d in WEEKDAYS if prof2023[d][h] is not None)
          / max(1, sum(prof2023[d][h] is not None for d in WEEKDAYS)), 1)
    for h in range(24)
]

main = years["2021"]
out = {
    "source": "TomTom Traffic Index, city page 'Moscow region (oblast)' (key RUS/Circle/moscow, circle r=23.1 km around 55.737,37.619)",
    "source_url": "https://www.tomtom.com/en_gb/traffic-index/page-data/moscow-traffic/page-data.json",
    "archived_url": "https://web.archive.org/web/20220214072249/https://www.tomtom.com/en_gb/traffic-index/page-data/moscow-traffic/page-data.json",
    "page_url": "https://www.tomtom.com/traffic-index/moscow-traffic/ (404 since 2023; Russia removed from the index)",
    "fetched": dt.date.today().isoformat(),
    "year": 2021,
    "definition": "congestion % = extra travel time vs free-flow (uncongested) travel time, TomTom definition; factor = 1 + congestion/100 = T(h)/T_freeflow",
    "hours": "local Moscow time (UTC+3), hour h = h:00-h:59",
    "weekday": {d: main["congestion_pct"][d] for d in DAYS + ["weekday_mean"]},
    "factor": {d: main["factor"][d] for d in DAYS + ["weekday_mean"]},
    "by_year": years,
    "cross_check_2023": {
        "source_url": "https://web.archive.org/web/20231203165234/https://api.midway.tomtom.com/ranking/liveHourly/RUS_moscow",
        "period": "2023-11-27..2023-12-01 (typical 'TravelTimeHistoric' curve for those weekdays, not the live values)",
        "free_flow_ref": f"min TravelTimeHistoric over Mon-Fri = {ff:.1f}",
        "congestion_pct": prof2023,
        "caveat": "one archived week, November-December (seasonal high), TomTom reference-trip index; only a sanity check",
    },
    "notes": [
        "No zone breakdown (center / TTK / MKAD) in TomTom data; only annual highway vs non-highway split.",
        "2020 is COVID-affected; 2019 and 2021 are the most representative years.",
        "license: TomTom website content, (c) TomTom; no open-data license. Use as published aggregate statistics with attribution.",
    ],
}
json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
print("2021 weekday factor 10..22:", main["factor"]["weekday_mean"][10:23])
print("2019 weekday factor 10..22:", years["2019"]["factor"]["weekday_mean"][10:23])
print("2023 weekday factor 10..22:", [round(1 + c / 100, 2) for c in prof2023["weekday_mean"][10:23]])
print("2021 weekday full:", main["congestion_pct"]["weekday_mean"])
print("2023 weekday full:", prof2023["weekday_mean"])
