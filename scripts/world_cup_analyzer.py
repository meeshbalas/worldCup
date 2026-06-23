import os
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

import pytz
import requests
from dateutil import parser as dtparser
from github import Github

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"


def now_in_tz(tz_name: str) -> datetime:
    tz = pytz.timezone(tz_name)
    return datetime.now(tz)


def should_run_now(tz_name: str, target_hour: int, target_minute: int) -> bool:
    n = now_in_tz(tz_name)
    return n.hour == target_hour and n.minute == target_minute


def est_day_bounds(tz_name: str) -> Tuple[datetime, datetime]:
    tz = pytz.timezone(tz_name)
    now_local = datetime.now(tz)
    start = tz.localize(datetime(now_local.year, now_local.month, now_local.day, 0, 0, 0))
    end = start + timedelta(days=1)
    return start, end


def safe_get(d: Dict[str, Any], *keys: str, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


class ProviderError(Exception):
    pass


class APIFootballClient:
    def __init__(self, key: str):
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": key})

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        r = self.session.get(f"{API_FOOTBALL_BASE}{path}", params=params, timeout=25)
        if r.status_code >= 400:
            raise ProviderError(f"API-Football error {r.status_code}: {r.text[:300]}")
        return r.json()

    def get_today_matches_world_cup(self, day_str: str) -> List[Dict[str, Any]]:
        # Fetch all fixtures for the date, then filter to World Cup matches.
        # This avoids hardcoding season=2022 and works for World Cup 2026 dates.
        data = self._get("/fixtures", {"date": day_str})
        fixtures = data.get("response", [])

        world_cup_fixtures = []
        for f in fixtures:
            league = f.get("league") or {}
            league_name = (league.get("name") or "").lower()
            if "world cup" in league_name:
                world_cup_fixtures.append(f)

        print(f"Fetched fixtures for {day_str}: {len(fixtures)} total")
        print(f"World Cup fixtures after filter: {len(world_cup_fixtures)}")
        return world_cup_fixtures

    def get_team_recent_form(self, team_id: int, season: int = 2022) -> Dict[str, Any]:
        # Keep season default for stats endpoint compatibility; can be tuned later.
        data = self._get("/teams/statistics", {"league": 1, "season": season, "team": team_id})
        return data.get("response", {})

    def get_fixture_players(self, fixture_id: int) -> List[Dict[str, Any]]:
        data = self._get("/fixtures/players", {"fixture": fixture_id})
        return data.get("response", [])


def normalize_probability(a: float, b: float, c: float) -> Tuple[float, float, float]:
    s = max(a + b + c, 1e-9)
    return (a / s, b / s, c / s)


def soft_confidence(gap: float, data_completeness: float, freshness: float) -> float:
    base = min(max(gap * 1.35, 0), 1)
    conf = 0.55 * base + 0.30 * data_completeness + 0.15 * freshness
    return round(conf * 100, 1)


def expected_goals_proxy(team_stats: Dict[str, Any]) -> float:
    goals_for = safe_get(team_stats, "goals", "for", "total", "total", default=0) or 0
    played = safe_get(team_stats, "fixtures", "played", "total", default=0) or 0
    if played <= 0:
        return 1.1
    return max(0.3, min(3.0, goals_for / played))


def outcome_probs(home_xg: float, away_xg: float) -> Tuple[float, float, float]:
    diff = home_xg - away_xg
    home = 0.40 + 0.22 * math.tanh(diff)
    away = 0.40 - 0.22 * math.tanh(diff)
    draw = 0.20 + 0.10 * math.exp(-abs(diff))
    return normalize_probability(home, draw, away)


def player_candidates_from_api_football(
    players_blob: List[Dict[str, Any]]
) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]], float]:
    scorers = []
    assisters = []
    sample = 0

    for team_block in players_blob:
        for p in team_block.get("players", []):
            sample += 1
            name = safe_get(p, "player", "name", default="Unknown")
            stats = (p.get("statistics") or [{}])[0]
            shots = safe_get(stats, "shots", "total", default=0) or 0
            key_passes = safe_get(stats, "passes", "key", default=0) or 0
            goals = safe_get(stats, "goals", "total", default=0) or 0
            assists = safe_get(stats, "goals", "assists", default=0) or 0

            scorer_score = 0.45 * shots + 0.35 * goals + 0.20 * key_passes
            assist_score = 0.50 * key_passes + 0.30 * assists + 0.20 * shots

            scorers.append((name, scorer_score))
            assisters.append((name, assist_score))

    def top_prob(lst: List[Tuple[str, float]], k=5) -> List[Tuple[str, float]]:
        lst = [(n, max(0.01, s)) for n, s in lst]
        total = sum(s for _, s in lst) or 1.0
        ranked = sorted([(n, s / total) for n, s in lst], key=lambda x: x[1], reverse=True)
        return [(n, round(p * 100, 1)) for n, p in ranked[:k]]

    completeness = min(1.0, sample / 22.0)
    return top_prob(scorers), top_prob(assisters), completeness


def fmt_est(iso_dt: str, tz_name: str) -> str:
    dt = dtparser.parse(iso_dt)
    target = pytz.timezone(tz_name)
    return dt.astimezone(target).strftime("%Y-%m-%d %I:%M %p %Z")


def create_or_update_issue(repo_full_name: str, token: str, title: str, body: str):
    gh = Github(token)
    repo = gh.get_repo(repo_full_name)
    open_issues = repo.get_issues(state="open")

    existing = None
    for i in open_issues:
        if i.title == title:
            existing = i
            break

    if existing:
        existing.edit(body=body)
        print(f"Updated issue #{existing.number}")
    else:
        created = repo.create_issue(title=title, body=body, labels=["world-cup", "daily-report"])
        print(f"Created issue #{created.number}")


def main():
    gh_token = os.getenv("GITHUB_TOKEN")
    repo_full_name = os.getenv("REPO_FULL_NAME")
    api_football_key = os.getenv("API_FOOTBALL_KEY")
    tz_target = os.getenv("TZ_TARGET", "America/New_York")
    target_hour = int(os.getenv("TARGET_HOUR", "9"))
    target_minute = int(os.getenv("TARGET_MINUTE", "0"))
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    force_run = os.getenv("FORCE_RUN", "false").lower() == "true"

    if not gh_token or not repo_full_name:
        raise RuntimeError("Missing required env vars: GITHUB_TOKEN and REPO_FULL_NAME")
    if not api_football_key:
        raise RuntimeError("Missing required env var: API_FOOTBALL_KEY")

    # Scheduled runs obey 9:00 ET gate. Manual runs always execute.
    if event_name != "workflow_dispatch" and not force_run:
        if not should_run_now(tz_target, target_hour, target_minute):
            print("Not target local time. Exiting.")
            return

    day_start, _ = est_day_bounds(tz_target)
    day_str = day_start.strftime("%Y-%m-%d")

    af_client = APIFootballClient(api_football_key)
    matches = []

    try:
        matches = af_client.get_today_matches_world_cup(day_str)
    except Exception as e:
        print(f"API-Football failed: {e}")

    title = f"World Cup Analyzer Report — {day_str} (ET)"
    lines = [
        f"# World Cup Daily Analyzer ({day_str} ET)",
        "",
        f"**Generated at:** {now_in_tz(tz_target).strftime('%Y-%m-%d %I:%M %p %Z')}",
        "**Provider used:** api-football",
        "",
    ]

    if not matches:
        lines += [
            "## Matches Today",
            "",
            "No World Cup matches found for today, or API-Football data unavailable.",
        ]
        create_or_update_issue(repo_full_name, gh_token, title, "\n".join(lines))
        return

    lines.append("## Matches Today (All times ET)")
    lines.append("")

    for m in matches:
        fixture_id = safe_get(m, "fixture", "id")
        kickoff_utc = safe_get(m, "fixture", "date", default="")
        home_name = safe_get(m, "teams", "home", "name", default="Home")
        away_name = safe_get(m, "teams", "away", "name", default="Away")
        home_id = safe_get(m, "teams", "home", "id")
        away_id = safe_get(m, "teams", "away", "id")

        home_stats, away_stats = {}, {}
        if home_id:
            try:
                home_stats = af_client.get_team_recent_form(home_id)
            except Exception:
                pass
        if away_id:
            try:
                away_stats = af_client.get_team_recent_form(away_id)
            except Exception:
                pass

        home_xg = expected_goals_proxy(home_stats)
        away_xg = expected_goals_proxy(away_stats)
        p_home, p_draw, p_away = outcome_probs(home_xg, away_xg)
        top_gap = sorted([p_home, p_draw, p_away], reverse=True)
        gap = top_gap[0] - top_gap[1]

        scorers, assisters = [], []
        completeness = 0.35
        if fixture_id:
            try:
                players_blob = af_client.get_fixture_players(fixture_id)
                scorers, assisters, completeness = player_candidates_from_api_football(players_blob)
            except Exception:
                pass

        freshness = 0.9
        outcome_conf = soft_confidence(gap, completeness, freshness)
        player_conf = round((0.6 * completeness + 0.4 * freshness) * 100, 1)

        lines += [
            f"### {home_name} vs {away_name}",
            f"- **Kickoff:** {fmt_est(kickoff_utc, tz_target) if kickoff_utc else 'TBD'}",
            f"- **Outcome probabilities:** {home_name} Win **{p_home*100:.1f}%** | Draw **{p_draw*100:.1f}%** | {away_name} Win **{p_away*100:.1f}%**",
            f"- **Outcome confidence:** **{outcome_conf}%**",
            f"- **Top likely scorers:** " + (", ".join([f"{n} ({p}%)" for n, p in scorers]) if scorers else "Data unavailable"),
            f"- **Top likely assisters:** " + (", ".join([f"{n} ({p}%)" for n, p in assisters]) if assisters else "Data unavailable"),
            f"- **Player prediction confidence:** **{player_conf}%**",
            "- **Key factors considered:** Team attacking rate proxy, relative matchup strength, available player shot/key-pass/goal contributions.",
            "",
        ]

    create_or_update_issue(repo_full_name, gh_token, title, "\n".join(lines))


if __name__ == "__main__":
    main()
