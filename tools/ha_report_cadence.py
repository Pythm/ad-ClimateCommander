#!/usr/bin/env python3
"""Find how often a sensor reports, from Home Assistant's history (read-only).

Used to fill the stale keys of ClimateCommander (`room_sensor_stale_minutes`, `backup_sensor_stale_minutes`,
`window_sensor_stale_minutes`), so that a sensor that is alive but reports rarely is not treated as offline.

Usage:
  python3 tools/ha_report_cadence.py sensor.room_temperature sensor.window_temperature [--days 14]

For every sensor it prints the number of reports per day, the median, 95th percentile and longest gap between reports,
the resolution (smallest step between values), a guess at the fixed heartbeat, the longest normal gap and the suggested
stale value: 1.5 x the longest normal gap, rounded to 5 minutes. The longest normal gap is the longest gap without a
break in the sorted gaps: a gap more than twice as long as the next shorter one (and more than 2 hours) counts as an
outage or a hole in the history, and those are listed. Read the list before using the value, and tell the user the values
and where they come from. Under 20 reports: ask the user instead.

Home Assistant keeps a row only when a value changes, and AppDaemon gets no event for a report with an unchanged value
either, so these gaps are the gaps the app sees. A heartbeat with an unchanged value shows up as a gap of twice the heartbeat.

Only reads. Uses ha_token and ha_url from a .local folder (made by tools/ha_snapshot.py in Pythm_AppdaemonApps): this
repository's .local, else ../Pythm_AppdaemonApps/.local, or the folder in the HA_LOCAL environment variable.
The token is never printed.
Home Assistant only keeps history for its recorder period (often 10 days), and sensors excluded from the
recorder have none. Python 3.8+, standard library only.
"""
import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_local():
    """ The first .local folder that has a token: HA_LOCAL, this repository, a Pythm_AppdaemonApps clone next to it. """
    candidates = [os.environ.get("HA_LOCAL"), os.path.join(ROOT, ".local"),
                  os.path.join(os.path.dirname(ROOT), "Pythm_AppdaemonApps", ".local")]
    for folder in candidates:
        if folder and os.path.isfile(os.path.join(folder, "ha_token")):
            return folder
    return os.path.join(ROOT, ".local")


LOCAL = find_local()
MIN_REPORTS = 20
BREAK_FACTOR = 2.0           # a gap this many times the next shorter one is not a normal gap ...
BREAK_MIN_MINUTES = 120      # ... when it is at least this long
HEARTBEAT_SHARE = 0.25       # the most common gap (to 30 s) is a heartbeat when this share of the gaps has it
STALE_FACTOR = 1.5


def read(name):
    path = os.path.join(LOCAL, name)
    if not os.path.isfile(path):
        sys.exit(f"{path} is missing. Run tools/ha_snapshot.py --init in Pythm_AppdaemonApps and follow its LLM_GUIDE.md first, or set HA_LOCAL.")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def percentile(values, share):
    return values[min(len(values) - 1, int(len(values) * share))]


def analyse(rows):
    """ rows: (datetime, state text) oldest first. Returns a dict with the numbers, or None without two numeric reports.
        A gap with an unavailable state in it is an outage of the sensor, not a gap between reports. """
    gaps, stamps, values, outages = [], [], [], []
    last, broken = None, False
    for when, state in rows:
        try:
            value = float(state)
        except (TypeError, ValueError):
            broken = True
            continue
        if last is not None:
            minutes = (when - last).total_seconds() / 60
            if broken:
                outages.append((when, minutes))
            else:
                gaps.append(minutes)
                stamps.append(when)
        last, broken = when, False
        values.append(value)
    if not gaps:
        return None
    ordered = sorted(gaps)
    normal = ordered[0]
    for gap in ordered[1:]:
        if gap > normal * BREAK_FACTOR and gap >= BREAK_MIN_MINUTES:
            break
        normal = gap
    outages += [(when, gap) for when, gap in zip(stamps, gaps) if gap > normal]
    used = [gap for gap in gaps if gap <= normal]
    buckets = {}
    for gap in gaps:
        buckets.setdefault(round(gap * 2), []).append(gap)
    top = max(buckets.values(), key = len)
    steps = [abs(b - a) for a, b in zip(values, values[1:]) if b != a]
    stale = max(5, round(normal * STALE_FACTOR / 5) * 5)
    return dict(reports = len(values), per_day = len(used) / (sum(used) / 1440) if sum(used) else 0.0, median = statistics.median(ordered),
                p95 = percentile(ordered, 0.95), longest = ordered[-1], heartbeat = statistics.median(top) if len(top) >= HEARTBEAT_SHARE * len(gaps) else None,
                resolution = round(min(steps), 4) if steps else None, normal = normal, stale = stale, outages = sorted(outages, key = lambda o: -o[1])[:5])


def describe(entity, result):
    if result is None:
        return f"{entity}: fewer than two numeric reports. Ask the user."
    beat = f"{result['heartbeat']:.1f} min" if result['heartbeat'] is not None else "none found (reports on change)"
    text = (f"{entity}: {result['reports']} reports, {result['per_day']:.1f}/day, gap median {result['median']:.1f} min, p95 {result['p95']:.1f} min, "
            f"longest {result['longest']:.1f} min, resolution {result['resolution']}, heartbeat {beat}, longest normal gap {result['normal']:.1f} min, "
            f"suggested stale value: {result['stale']}")
    if result['outages']:
        text += "\n  not counted as normal (outage or history hole): " + ", ".join(f"{m:.0f} min ending {w:%Y-%m-%d %H:%M} UTC" for w, m in result['outages'])
    if result['reports'] < MIN_REPORTS:
        text += f"\n  only {result['reports']} reports: too few for a suggestion. Ask the user."
    return text


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("entities", nargs="+", help="sensor entity ids")
    ap.add_argument("--days", type=int, default=14, help="how far back to look (default 14)")
    args = ap.parse_args()
    r = subprocess.run(["git", "-C", os.path.dirname(LOCAL), "check-ignore", "-q", os.path.join(LOCAL, "ha_token")])
    if r.returncode == 1:
        sys.exit("git does not ignore .local/ha_token. Fix .gitignore first.")
    url, token = read("ha_url").rstrip("/"), read("ha_token")
    now = dt.datetime.now(dt.timezone.utc)
    start = (now - dt.timedelta(days=args.days)).isoformat()
    query = urllib.parse.urlencode({"filter_entity_id": ",".join(args.entities), "end_time": now.isoformat()})
    req = urllib.request.Request(f"{url}/api/history/period/{urllib.parse.quote(start)}?{query}",
                                 headers={"Authorization": "Bearer " + token})
    data = json.load(urllib.request.urlopen(req, timeout=180))
    found = set()
    for series in data:
        if not series:
            continue
        entity = series[0]["entity_id"]
        found.add(entity)
        rows = [(dt.datetime.fromisoformat(s["last_changed"].replace("Z", "+00:00")), s["state"]) for s in series]
        print(describe(entity, analyse(rows)))
    for entity in args.entities:
        if entity not in found:
            print(f"{entity}: no history (excluded from the recorder, wrong id, or never changed). Ask the user.")


if __name__ == "__main__":
    main()
