"""
Download boys varsity basketball games from the MHSAA Score Center into games_<season>.csv.

    python fetch_games.py              # current season
    python fetch_games.py 2024 2025    # specific seasons

A season is named by the year it starts: games_2025.csv is the 2025-26 season
(November 2025 through April 2026). Each file holds every game of the season:
finished games with scores and upcoming games with blank scores (so the schedule
fills in automatically), plus tournament games once they are scheduled. Out-of-state
teams keep MHSAA's state tag, e.g. "Toledo St John's Jesuit (OH)".
"""
import csv
import json
import re
import sys
import urllib.request
from datetime import date

URL = ("https://my.mhsaa.com//DesktopModules/MHSAA-Endpoint/handlers/ContestWithParticipantDepotSearch.ashx"
       "?Method=ScoreCenterSearch&StartDate={start}&EndDate={end}&Sport={sport}&Level=V")
SPORT = "BBB"  # boys basketball ("GBB" is girls)
FIELDS = ["date", "home", "away", "home_score", "away_score"]

# Level tags MHSAA sometimes appends to a team name, e.g. "Coldwater (V)"
LEVEL_TAG = re.compile(r"\s*\((V|BV|JV|FR)\)\s*$")
# Numbered state tags, e.g. "(OH-2)" -> "(OH)"
NUMBERED_STATE = re.compile(r"\(([A-Z]{2})-\d+\)\s*$")


STATE_TAG = re.compile(r"\(([A-Za-z]{2,4})\)$")


def is_oos(name):
    """True for teams tagged with a non-Michigan state, e.g. "Clyde (OH)"."""
    m = STATE_TAG.search((name or "").strip())
    return bool(m) and m.group(1).upper() != "MI"


def clean_name(name):
    name = (name or "").strip()
    name = LEVEL_TAG.sub("", name)
    name = NUMBERED_STATE.sub(r"(\1)", name)
    return re.sub(r"\s+", " ", name).strip()


def score(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else ""


def fetch_season(season):
    """All varsity games from November of `season` through April of the next year."""
    url = URL.format(start=f"{season}-11-01", end=f"{int(season) + 1}-04-30", sport=SPORT)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (mi-football-predictor)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        rows = json.load(resp)

    games = {}
    for r in rows:
        home, away = clean_name(r.get("HostTeamName")), clean_name(r.get("AwayTeamName"))
        day = (r.get("ContestDateWithYear") or "")[:10]
        if not home or not away or not day:
            continue
        hs, as_ = score(r.get("HostScore")), score(r.get("AwayScore"))
        if hs != "" and as_ != "" and hs == as_:
            # Unreported results sometimes show as 0-0; basketball games can't end tied
            hs = as_ = ""
        key = (day,) + tuple(sorted([home, away]))
        game = {"date": day, "home": home, "away": away, "home_score": hs, "away_score": as_}
        # The same game can be listed twice; keep the version that has a score
        if key not in games or (games[key]["home_score"] == "" and hs != ""):
            games[key] = game
    return sorted(games.values(), key=lambda g: (g["date"], g["home"], g["away"]))


def read_existing(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def describe_changes(old, new):
    key = lambda g: (g["date"],) + tuple(sorted([g["home"], g["away"]]))
    before = {key(g): g for g in old}
    added = scored = changed = 0
    for k, g in ((key(g), g) for g in new):
        o = before.get(k)
        if o is None:
            added += 1
        elif str(o["home_score"]) == "" and g["home_score"] != "":
            scored += 1
        elif (o["home"], str(o["home_score"]), str(o["away_score"])) != (g["home"], str(g["home_score"]), str(g["away_score"])):
            changed += 1
    removed = len(set(before) - {key(g) for g in new})
    return f"{added} new, {scored} newly scored, {changed} corrected, {removed} removed"


def main(seasons):
    for season in seasons:
        path = f"games_{season}.csv"
        games = fetch_season(season)
        if not games:
            print(f"{season}: MHSAA returned no games, leaving {path} unchanged")
            continue
        old = read_existing(path)
        # MHSAA only lists games involving Michigan schools. Keep any out-of-state vs.
        # out-of-state games already in the file (added by hand to rate those opponents).
        have = {(g["date"],) + tuple(sorted([g["home"], g["away"]])) for g in games}
        kept = [{k: g[k] for k in FIELDS} for g in old
                if is_oos(g["home"]) and is_oos(g["away"])
                and (g["date"],) + tuple(sorted([g["home"], g["away"]])) not in have]
        games = sorted(games + kept, key=lambda g: (g["date"], g["home"], g["away"]))
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(games)
        played = sum(1 for g in games if g["home_score"] != "")
        print(f"{season}: {len(games)} games ({played} played) -> {path}  [{describe_changes(old, games)}]")


def current_season(today=None):
    """Seasons start in November; from August on, the upcoming season is current."""
    today = today or date.today()
    return str(today.year if today.month >= 8 else today.year - 1)


if __name__ == "__main__":
    args = sys.argv[1:] or [current_season()]
    main(args)
