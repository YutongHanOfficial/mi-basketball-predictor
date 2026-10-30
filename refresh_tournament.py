"""
Build tournament_<season>.csv from MHSAA's boys basketball tournament feed.

    python refresh_tournament.py              # current season
    python refresh_tournament.py 2023 2024    # past seasons

Every school plays in the MHSAA tournament, so the feed gives each school's
division (1-4) and how far it went. One row per school:
    school      as in games_<season>.csv
    division    1-4 (2019-20 on), or Class A-D before that
    round       last round played (1 = District First Round ... 8 = Final)
    round_name  e.g. "Regional Final"
    result      W, L, or blank when that game hasn't been played yet

MHSAA's feed covers 2019-20 onward (2022-23 is missing). For finished seasons it
doesn't cover, the tournament is rebuilt from the games in games_<season>.csv
(see from_games). Until the brackets are drawn (late February) the feed has no
teams and nothing is written.
"""
import json
import os
import sys
import urllib.error
import urllib.request

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from fetch_games import current_season, is_oos

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


COLUMNS = ["school", "division", "round", "round_name", "result"]
# Rounds after the District Final, in order
LATE_ROUNDS = ["Regional Semifinal", "Regional Final", "Quarterfinal", "Semifinal", "Final"]
# School enrollments by season (from MHSAA's football playoff-point lists), used to
# tell the four brackets apart: the largest schools play in Division 1 / Class A
ENROLLMENT_FILE = "enrollment.csv"


def from_games(season, enrollment=None):
    """Rebuild a finished season's tournament from games_<season>.csv.

    The District Final day is the last day before the Regional Semifinals (~64
    games) with ~128 games; Districts are played Monday through that Friday. The
    games in that window form one small group per District, whose last game is its
    Final. After that every surviving school plays once per round, so a school's
    k-th game after its District Final is LATE_ROUNDS[k-1]. The four brackets are the
    connected groups of tournament games, numbered by their schools' median
    enrollment (Division 1-4 from 2019-20, Class A-D before)."""
    g = pd.read_csv(f"games_{season}.csv").dropna(subset=["home_score", "away_score"])
    g = g[~g["home"].map(is_oos) & ~g["away"].map(is_oos) & (g["home_score"] != g["away_score"])].copy()
    g["d"] = pd.to_datetime(g["date"])
    late = g[g["d"] >= pd.Timestamp(f"{int(season) + 1}-02-15")]
    per_day = late.groupby("d").size()
    days = list(per_day.index)
    # Regional Semifinals: ~64 games, then ~32 Regional Finals within the next few days
    # (sometimes split over two days)
    reg_semi = next((d for d in days if 50 <= per_day[d] <= 72
                     and 24 <= per_day[(per_day.index > d) & (per_day.index <= d + pd.Timedelta(days=4))].sum() <= 40), None)
    if reg_semi is None:
        return pd.DataFrame(columns=COLUMNS)
    dist_final = max((d for d in days if d < reg_semi and 100 <= per_day[d] <= 140), default=None)
    if dist_final is None:
        return pd.DataFrame(columns=COLUMNS)
    dist = late[(late["d"] > dist_final - pd.Timedelta(days=6)) & (late["d"] <= dist_final)]
    after = late[late["d"] > dist_final].sort_values("d")
    games = pd.concat([dist, after])

    teams = sorted(set(games["home"]) | set(games["away"]))
    ix = {t: i for i, t in enumerate(teams)}
    def components(df):
        m = coo_matrix((np.ones(len(df)), (df["home"].map(ix), df["away"].map(ix))), shape=(len(teams), len(teams)))
        return connected_components(m, directed=False)[1]
    district_of, bracket_of = components(dist), components(games)

    # Number the brackets by enrollment, largest first
    enr = {}
    if enrollment is not None and len(enrollment):
        nearest = min(enrollment["season"].unique(), key=lambda s: abs(int(s) - int(season)))
        enr = enrollment[enrollment["season"] == nearest].set_index("school")["enrollment"].to_dict()
    groups = pd.Series(bracket_of[[ix[t] for t in teams]], index=teams)
    big = groups.value_counts().index[:4]  # the four brackets (stray games form tiny groups)
    size = {b: np.nanmedian([enr.get(t, np.nan) for t in groups.index[groups == b]] or [np.nan]) for b in big}
    order = sorted(big, key=lambda b: -size[b] if not np.isnan(size[b]) else 0)
    names = [1, 2, 3, 4] if int(season) >= 2019 else ["A", "B", "C", "D"]
    division = dict(zip(order, names))

    last = {}
    def record(team, rnd, name, won):
        if groups[team] in division:
            last[team] = {"school": team, "division": division[groups[team]], "round": rnd,
                          "round_name": name, "result": "W" if won else "L"}
    # District games; the latest game in each District is its Final
    dist = dist.sort_values("d").copy()
    dist["is_final"] = dist["d"] == dist.groupby(dist["home"].map(lambda t: district_of[ix[t]]))["d"].transform("max")
    for r in dist.itertuples():
        home_won = r.home_score > r.away_score
        name, rnd = ("District Final", 3) if r.is_final else ("District", 2)
        record(r.home, rnd, name, home_won)
        record(r.away, rnd, name, not home_won)
    # Later rounds: one game per round for each school still alive
    played = {}
    for r in after.itertuples():
        home_won = r.home_score > r.away_score
        for team, won in ((r.home, home_won), (r.away, not home_won)):
            k = played.get(team, 0)
            if k < len(LATE_ROUNDS):
                record(team, 4 + k, LATE_ROUNDS[k], won)
            played[team] = k + 1
    return pd.DataFrame(list(last.values()), columns=COLUMNS)


def main(seasons, infer=True):
    enrollment = pd.read_csv(ENROLLMENT_FILE, dtype={"season": str}) if os.path.exists(ENROLLMENT_FILE) else None
    for season in seasons:
        out = parse(fetch_tournament(season))
        if out.empty and infer and str(season) < current_season():
            out = from_games(season, enrollment)
            if not out.empty:
                print(f"{season}: not in MHSAA's tournament feed; rebuilt from the game data")
        if out.empty:
            print(f"{season}: no tournament data from MHSAA; nothing written")
            continue
        path = f"tournament_{season}.csv"
        out.sort_values(["division", "school"]).to_csv(path, index=False)
        champs = out[(out["round"] == out["round"].max()) & (out["result"] == "W")].sort_values("division")
        names = ", ".join(f"{r.school} ({r.division})" for r in champs.itertuples())
        print(f"{season}: {len(out)} schools -> {path}; champions: {names or 'not decided yet'}")


if __name__ == "__main__":
    main(sys.argv[1:] or [current_season()])
