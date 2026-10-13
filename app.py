"""Daily football-corners report for Telegram.

The script uses API-Football and deliberately keeps the scoring model simple:
team home/away averages are the main signal and recent head-to-head matches
are a smaller supporting signal. It sends only matches that pass both filters:
expected corners > 10 and expected goals > 2.5.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BASE_URL = "https://v3.football.api-sports.io"
FINISHED_STATUSES = {"FT", "AET", "PEN"}
UPCOMING_STATUSES = {"NS", "TBD"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def current_season() -> int:
    """Football seasons normally start in the second half of the year."""
    today = datetime.now(ZoneInfo(os.getenv("TIMEZONE", "Europe/Warsaw")))
    return today.year if today.month >= 7 else today.year - 1


def chunks(items: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("%", "").replace(",", ".").strip()
    try:
        return float(text)
    except ValueError:
        return None


def stat_value(statistics: list[dict[str, Any]], *names: str) -> float | None:
    wanted = {name.casefold() for name in names}
    for item in statistics:
        if str(item.get("type", "")).casefold() in wanted:
            return number(item.get("value"))
    return None


@dataclass
class Summary:
    sample: int
    corners_for: float
    corners_against: float
    goals_for: float
    goals_against: float


class ApiFootball:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.cache: dict[str, list[dict[str, Any]]] = {}

    def get(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{BASE_URL}{path}?{query}"
        request = Request(url, headers={"x-apisports-key": self.api_key})
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"API-Football request failed: {exc}") from exc

        errors = payload.get("errors") or {}
        if errors:
            raise RuntimeError(f"API-Football error: {errors}")
        return payload.get("response", [])

    def upcoming(self, league_id: int, season: int, day: date) -> list[dict[str, Any]]:
        return self.get(
            "/fixtures",
            {"league": league_id, "season": season, "date": day.isoformat()},
        )

    def team_history(self, team_id: int, last: int) -> list[dict[str, Any]]:
        key = f"team:{team_id}:{last}"
        if key not in self.cache:
            self.cache[key] = self.get("/fixtures", {"team": team_id, "last": last})
        return self.cache[key]

    def head_to_head(self, home_id: int, away_id: int, last: int) -> list[dict[str, Any]]:
        key = f"h2h:{home_id}-{away_id}:{last}"
        if key not in self.cache:
            self.cache[key] = self.get(
                "/fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": last}
            )
        return self.cache[key]

    def details(self, fixture_ids: list[int]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        missing = [fixture_id for fixture_id in fixture_ids if f"fixture:{fixture_id}" not in self.cache]
        for batch in chunks(missing, 20):
            fixtures = self.get("/fixtures", {"ids": "-".join(map(str, batch))})
            for fixture in fixtures:
                fixture_id = int(fixture["fixture"]["id"])
                self.cache[f"fixture:{fixture_id}"] = [fixture]
        for fixture_id in fixture_ids:
            cached = self.cache.get(f"fixture:{fixture_id}", [])
            if cached:
                result[fixture_id] = cached[0]
        return result


def fixture_is_finished(fixture: dict[str, Any]) -> bool:
    return fixture.get("fixture", {}).get("status", {}).get("short") in FINISHED_STATUSES


def fixture_team_ids(fixture: dict[str, Any]) -> tuple[int, int]:
    teams = fixture["teams"]
    return int(teams["home"]["id"]), int(teams["away"]["id"])


def team_fixture_stats(fixture: dict[str, Any], team_id: int) -> tuple[float, float] | None:
    """Return (corners_for, corners_against), or None when stats are missing."""
    home_id, away_id = fixture_team_ids(fixture)
    by_team: dict[int, list[dict[str, Any]]] = {}
    for team_block in fixture.get("statistics", []):
        block_team_id = team_block.get("team", {}).get("id")
        if block_team_id is not None:
            by_team[int(block_team_id)] = team_block.get("statistics", [])

    own = by_team.get(team_id)
    opponent_id = away_id if team_id == home_id else home_id
    opponent = by_team.get(opponent_id)
    if own is None or opponent is None:
        return None
    own_corners = stat_value(own, "Corner Kicks", "Corners")
    opponent_corners = stat_value(opponent, "Corner Kicks", "Corners")
    if own_corners is None or opponent_corners is None:
        return None
    return own_corners, opponent_corners


def summarize(
    fixtures: list[dict[str, Any]],
    team_id: int,
    details: dict[int, dict[str, Any]],
    venue: str | None = None,
    minimum_sample: int = 3,
) -> Summary | None:
    selected: list[dict[str, Any]] = []
    for fixture in fixtures:
        if not fixture_is_finished(fixture):
            continue
        home_id, away_id = fixture_team_ids(fixture)
        if team_id not in {home_id, away_id}:
            continue
        if venue and ("home" if home_id == team_id else "away") != venue:
            continue
        selected.append(fixture)

    # If the last N fixtures do not contain enough home/away matches, use all
    # available recent matches instead of silently dropping the team.
    if venue and len(selected) < minimum_sample:
        return summarize(fixtures, team_id, details, venue=None, minimum_sample=minimum_sample)

    rows: list[tuple[float, float, float, float]] = []
    for fixture in selected:
        fixture_id = int(fixture["fixture"]["id"])
        detailed = details.get(fixture_id)
        if not detailed:
            continue
        corner_pair = team_fixture_stats(detailed, team_id)
        goals = detailed.get("goals", {})
        home_id, _ = fixture_team_ids(detailed)
        own_goals = number(goals.get("home" if home_id == team_id else "away"))
        opponent_goals = number(goals.get("away" if home_id == team_id else "home"))
        if corner_pair is None or own_goals is None or opponent_goals is None:
            continue
        rows.append((*corner_pair, own_goals, opponent_goals))

    if not rows:
        return None
    return Summary(
        sample=len(rows),
        corners_for=mean(row[0] for row in rows),
        corners_against=mean(row[1] for row in rows),
        goals_for=mean(row[2] for row in rows),
        goals_against=mean(row[3] for row in rows),
    )


def model_match(
    home_summary: Summary,
    away_summary: Summary,
    h2h_summary: Summary | None,
) -> tuple[float, float]:
    base_corners = (
        (home_summary.corners_for + away_summary.corners_against) / 2
        + (away_summary.corners_for + home_summary.corners_against) / 2
    )
    base_goals = (
        (home_summary.goals_for + away_summary.goals_against) / 2
        + (away_summary.goals_for + home_summary.goals_against) / 2
    )

    if h2h_summary and h2h_summary.sample >= 3:
        corners = base_corners * 0.8 + (h2h_summary.corners_for + h2h_summary.corners_against) * 0.2
        goals = base_goals * 0.8 + (h2h_summary.goals_for + h2h_summary.goals_against) * 0.2
        return corners, goals
    return base_corners, base_goals


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def report_for_match(
    fixture: dict[str, Any],
    home_summary: Summary,
    away_summary: Summary,
    h2h_summary: Summary | None,
    expected_corners: float,
    expected_goals: float,
) -> str:
    home_name = fixture["teams"]["home"]["name"]
    away_name = fixture["teams"]["away"]["name"]
    kickoff = fixture["fixture"]["date"].replace("T", " ")[:16]
    h2h_corners = None
    h2h_goals = None
    if h2h_summary:
        h2h_corners = h2h_summary.corners_for + h2h_summary.corners_against
        h2h_goals = h2h_summary.goals_for + h2h_summary.goals_against

    lines = [
        f"🔥 {home_name} – {away_name}",
        f"🗓 {kickoff} UTC",
        "",
        "                         Gospodarze  Goście      H2H",
        f"Rzuty rożne             {fmt(home_summary.corners_for):>10}  {fmt(away_summary.corners_for):>8}  {fmt(h2h_corners):>8}",
        f"Gole                    {fmt(home_summary.goals_for):>10}  {fmt(away_summary.goals_for):>8}  {fmt(h2h_goals):>8}",
        "",
        f"Model rożnych: {expected_corners:.1f} ✅",
        f"Model goli:    {expected_goals:.1f} ✅",
        f"Próba: {home_summary.sample} / {away_summary.sample} meczów; H2H: {h2h_summary.sample if h2h_summary else 0}",
    ]
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, message: str) -> None:
    payload = urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Telegram request failed: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def run() -> None:
    api_key = os.getenv("API_FOOTBALL_KEY")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not api_key or not telegram_token or not telegram_chat_id:
        raise RuntimeError(
            "Set API_FOOTBALL_KEY, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before running."
        )

    league_ids = [int(value.strip()) for value in os.getenv("LEAGUE_IDS", "106").split(",") if value.strip()]
    season = env_int("SEASON", current_season())
    lookahead_days = env_int("LOOKAHEAD_DAYS", 1)
    history_matches = env_int("HISTORY_MATCHES", 10)
    h2h_matches = env_int("H2H_MATCHES", 5)
    min_sample = env_int("MIN_SAMPLE", 3)
    corners_threshold = env_float("CORNERS_THRESHOLD", 10.0)
    goals_threshold = env_float("GOALS_THRESHOLD", 2.5)
    timezone = ZoneInfo(os.getenv("TIMEZONE", "Europe/Warsaw"))
    today = datetime.now(timezone).date()

    api = ApiFootball(api_key)
    upcoming: dict[int, dict[str, Any]] = {}
    for offset in range(lookahead_days + 1):
        day = today + timedelta(days=offset)
        for league_id in league_ids:
            for fixture in api.upcoming(league_id, season, day):
                status = fixture.get("fixture", {}).get("status", {}).get("short")
                if status in UPCOMING_STATUSES:
                    upcoming[int(fixture["fixture"]["id"])] = fixture

    if not upcoming:
        send_telegram(telegram_token, telegram_chat_id, "⚽ Brak zaplanowanych meczów w wybranym okresie.")
        return

    history_by_team: dict[int, list[dict[str, Any]]] = {}
    h2h_by_fixture: dict[int, list[dict[str, Any]]] = {}
    detail_ids: set[int] = set()
    for fixture_id, fixture in upcoming.items():
        home_id, away_id = fixture_team_ids(fixture)
        for team_id in (home_id, away_id):
            history_by_team.setdefault(team_id, api.team_history(team_id, history_matches))
            detail_ids.update(int(item["fixture"]["id"]) for item in history_by_team[team_id])
        h2h = api.head_to_head(home_id, away_id, h2h_matches)
        h2h_by_fixture[fixture_id] = h2h
        detail_ids.update(int(item["fixture"]["id"]) for item in h2h)

    details = api.details(sorted(detail_ids))
    selected: list[tuple[float, str]] = []
    failures = 0
    for fixture_id, fixture in upcoming.items():
        home_id, away_id = fixture_team_ids(fixture)
        try:
            home_summary = summarize(
                history_by_team[home_id], home_id, details, venue="home", minimum_sample=min_sample
            )
            away_summary = summarize(
                history_by_team[away_id], away_id, details, venue="away", minimum_sample=min_sample
            )
            h2h_summary = summarize(h2h_by_fixture[fixture_id], home_id, details, minimum_sample=min_sample)
            if not home_summary or not away_summary:
                continue
            expected_corners, expected_goals = model_match(home_summary, away_summary, h2h_summary)
            if expected_corners <= corners_threshold or expected_goals <= goals_threshold:
                continue
            score = (expected_corners - corners_threshold) + 2 * (expected_goals - goals_threshold)
            selected.append(
                (
                    score,
                    report_for_match(
                        fixture,
                        home_summary,
                        away_summary,
                        h2h_summary,
                        expected_corners,
                        expected_goals,
                    ),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            failures += 1
            print(f"Skipping fixture {fixture_id}: {exc}", file=sys.stderr)

    selected.sort(key=lambda item: item[0], reverse=True)
    max_matches = env_int("MAX_MATCHES", 5)
    if selected:
        message = "\n\n".join(report for _, report in selected[:max_matches])
        message = "📊 NAJCIEKAWSZE MECZE DNIA\n\n" + message
    else:
        message = (
            "📊 NAJCIEKAWSZE MECZE DNIA\n\n"
            f"Brak meczów spełniających warunki: rożne > {corners_threshold:g} "
            f"i gole > {goals_threshold:g}."
        )
    if failures:
        print(f"Skipped fixtures due to incomplete data: {failures}", file=sys.stderr)
    send_telegram(telegram_token, telegram_chat_id, message[:4090])


if __name__ == "__main__":
    run()
