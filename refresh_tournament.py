"""
Build tournament_<season>.csv from MHSAA's boys basketball tournament feed.

    python refresh_tournament.py              # current season
    python refresh_tournament.py 2023 2024    # past seasons

Every school plays in the MHSAA tournament, so the feed gives each school's
division (1-4) and how far it went. One row per school:
    school      as in games_<season>.csv
    division    1-4
    round       last round played (1 = District First Round ... 8 = Final)
    round_name  e.g. "Regional Final"
    result      W, L, or blank when that game hasn't been played yet

MHSAA's feed covers 2019-20 onward (2022-23 is missing). Until the brackets are
drawn (late February) it has no teams and nothing is written.
"""
import json
import sys
import urllib.error
import urllib.request

import pandas as pd

from fetch_games import current_season

API = "https://my.mhsaa.com/DesktopModules/MHSAA-Endpoint/API/"


def fetch(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_tournament(season):
    """The tournament for the season starting in `season` (MHSAA files it under that year)."""
    try:
        season_id = fetch(API + "SportSeason/Search", {"Year": str(season), "Sport": "BB", "Gender": "M"})
        return fetch(f"{API}Tournament/TournamentData?id={season_id}") if season_id else {}
    except urllib.error.HTTPError as e:
        if e.code in (404, 500):  # no tournament on file for that year
            return {}
        raise


def parse(data):
    """TournamentData JSON -> one row per school with its last game."""
    last = {}
    for div in (data or {}).get("Divisions") or []:
        for rnd in div["Rounds"]:
            for b in rnd["Brackets"]:
                c = b["Contest"]
                for side in ("HomeTeam", "AwayTeam"):
                    t = c.get(side) or {}
                    name = (t.get("Name") or "").strip()
                    if not name:
                        continue
                    code = (t.get("WinLossCode") or "").strip()
                    row = {"school": name, "division": int(div["DivisionId"]), "round": int(rnd["RoundNumber"]),
                           "round_name": b.get("RoundTitle") or "",
                           "result": "W" if code == "W" else "L" if code in ("L", "LF") else ""}
                    if name not in last or row["round"] >= last[name]["round"]:
                        last[name] = row
    return pd.DataFrame(list(last.values()), columns=["school", "division", "round", "round_name", "result"])


def main(seasons):
    for season in seasons:
        out = parse(fetch_tournament(season))
        if out.empty:
            print(f"{season}: no tournament data from MHSAA; nothing written")
            continue
        path = f"tournament_{season}.csv"
        out.sort_values(["division", "school"]).to_csv(path, index=False)
        champs = out[(out["round"] == out["round"].max()) & (out["result"] == "W")]
        print(f"{season}: {len(out)} schools -> {path}; champions: {', '.join(champs['school']) or 'not decided yet'}")


if __name__ == "__main__":
    main(sys.argv[1:] or [current_season()])
