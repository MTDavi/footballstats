"""Daily Danish Superliga corners and goals report for Telegram.

The script uses Sportmonks Football API. It compares home-team, away-team
and head-to-head averages, then sends only matches above the configured
corners and goals thresholds.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BASE_URL = "https://api.sportmonks.com/v3/football"
FINISHED_STATE_IDS = {5, 7, 8}  # FT, AET, FT_PEN
UPCOMING_STATE_IDS = {1}  # NS
CORNERS_TYPE_ID = 34


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


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


class Sportmonks:
    def __init__(self, token: str) -> None:
        self.token = token

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "Authorization": self.token,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(body)
            except json.JSONDecodeError:
                detail = body[:500]
            raise RuntimeError(f"Sportmonks request failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Sportmonks request failed: {exc}") from exc

        errors = payload.get("errors") or {}
        if errors:
            raise RuntimeError(f"Sportmonks error: {errors}")
        data = payload.get("data", [])
        if isinstance(data, dict):
            return [data]
        return data

    def upcoming(self, start: date, end: date) -> list[dict[str, Any]]:
        return self.get(
            f"/fixtures/between/{start.isoformat()}/{end.isoformat()}",
            {"include": "participants;state", "per_page": 50, "order": "asc"},
        )

    def team_history(self, team_id: int, today: date, league_id: int) -> list[dict[str, Any]]:
        """Fetch up to the requested recent fixtures in 100-day API windows."""
        oldest = today - timedelta(days=370)
        window_end = today - timedelta(days=1)
        fixtures: list[dict[str, Any]] = []
        while window_end >= oldest:
            window_start = max(oldest, window_end - timedelta(days=99))
            fixtures.extend(
                self.get(
                    f"/fixtures/between/{window_start.isoformat()}/{window_end.isoformat()}/{team_id}",
                    {
                        "include": "statistics;scores;participants;state",
                        "filters": f"fixtureStatisticTypes:{CORNERS_TYPE_ID}",
                        "per_page": 50,
                        "order": "desc",
                    },
                )
            )
            window_end = window_start - timedelta(days=1)
        return [
            fixture
            for fixture in fixtures
            if int(fixture.get("league_id", 0)) == league_id and fixture_is_finished(fixture)
        ]

    def head_to_head(
        self, home_id: int, away_id: int, league_id: int, last: int
    ) -> list[dict[str, Any]]:
        return self.get(
            f"/fixtures/head-to-head/{home_id}/{away_id}",
            {
                "include": "statistics;scores;participants;state",
                "filters": f"fixtureLeagues:{league_id}",
                "per_page": last,
                "order": "desc",
            },
        )[:last]


@dataclass
class Summary:
    sample: int
    corners_for: float
    corners_against: float
    goals_for: float
    goals_against: float


def fixture_participants(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    participants = fixture.get("participants") or []
    return [item for item in participants if isinstance(item, dict)]


def fixture_team_ids(fixture: dict[str, Any]) -> tuple[int, int]:
    participants = fixture_participants(fixture)
    home: int | None = None
    away: int | None = None
    for participant in participants:
        participant_id = participant.get("id")
        if participant_id is None:
            continue
        location = (participant.get("meta") or {}).get("location")
        if location == "home":
            home = int(participant_id)
        elif location == "away":
            away = int(participant_id)
    if home is None or away is None:
        if len(participants) >= 2:
            home = int(participants[0]["id"])
            away = int(participants[1]["id"])
        else:
            raise KeyError("Fixture has no two participants")
    return home, away


def fixture_team_names(fixture: dict[str, Any]) -> tuple[str, str]:
    participants = fixture_participants(fixture)
    names: dict[str, str] = {}
    for participant in participants:
        location = (participant.get("meta") or {}).get("location")
        if location in {"home", "away"}:
            names[location] = str(participant.get("name", "Unknown"))
    if "home" not in names or "away" not in names:
        if len(participants) >= 2:
            return str(participants[0].get("name", "Unknown")), str(participants[1].get("name", "Unknown"))
        raise KeyError("Fixture has no team names")
    return names["home"], names["away"]


def fixture_is_finished(fixture: dict[str, Any]) -> bool:
    return int(fixture.get("state_id", 0)) in FINISHED_STATE_IDS


def fixture_is_upcoming(fixture: dict[str, Any]) -> bool:
    return int(fixture.get("state_id", 0)) in UPCOMING_STATE_IDS


def team_fixture_stats(
    fixture: dict[str, Any], team_id: int
) -> tuple[float, float] | None:
    """Return (corners_for, corners_against), or None when unavailable."""
    home_id, away_id = fixture_team_ids(fixture)
    by_team: dict[int, float] = {}
    by_location: dict[str, float] = {}
    for item in fixture.get("statistics", []) or []:
        if int(item.get("type_id", 0)) != CORNERS_TYPE_ID:
            continue
        data = item.get("data") or {}
        value = number(data.get("value") if isinstance(data, dict) else data)
        if value is None:
            continue
        participant_id = item.get("participant_id")
        if participant_id is not None:
            by_team[int(participant_id)] = value
        location = item.get("location")
        if location in {"home", "away"}:
            by_location[location] = value

    own = by_team.get(team_id)
    opponent_id = away_id if team_id == home_id else home_id
    opponent = by_team.get(opponent_id)
    if own is None or opponent is None:
        own_location = "home" if team_id == home_id else "away"
        opponent_location = "away" if own_location == "home" else "home"
        own = by_location.get(own_location)
        opponent = by_location.get(opponent_location)
    if own is None or opponent is None:
        return None
    return own, opponent


def fixture_goals(
    fixture: dict[str, Any], team_id: int
) -> tuple[float, float] | None:
    """Return (goals_for, goals_against) from the final/current score."""
    home_id, away_id = fixture_team_ids(fixture)
    scores = fixture.get("scores", []) or []
    preferred = [item for item in scores if item.get("description") == "CURRENT"]
    if not preferred:
        preferred = [
            item
            for item in scores
            if item.get("description") in {"2ND_HALF", "AET", "FT_PEN"}
        ]
    by_team: dict[int, float] = {}
    by_location: dict[str, float] = {}
    for item in preferred:
        score = item.get("score") or {}
        goals = number(score.get("goals"))
        if goals is None:
            continue
        participant_id = item.get("participant_id")
        if participant_id is not None:
            by_team[int(participant_id)] = goals
        location = score.get("participant")
        if location in {"home", "away"}:
            by_location[location] = goals

    own = by_team.get(team_id)
    opponent_id = away_id if team_id == home_id else home_id
    opponent = by_team.get(opponent_id)
    if own is None or opponent is None:
        own_location = "home" if team_id == home_id else "away"
        opponent_location = "away" if own_location == "home" else "home"
        own = by_location.get(own_location)
        opponent = by_location.get(opponent_location)
    if own is None or opponent is None:
        return None
    return own, opponent


def summarize(
    fixtures: list[dict[str, Any]],
    team_id: int,
    venue: str | None = None,
    minimum_sample: int = 3,
) -> Summary | None:
    selected: list[dict[str, Any]] = []
    for fixture in fixtures:
        if not fixture_is_finished(fixture):
            continue
        try:
            home_id, away_id = fixture_team_ids(fixture)
        except (KeyError, TypeError, ValueError):
            continue
        if team_id not in {home_id, away_id}:
            continue
        if venue and ("home" if home_id == team_id else "away") != venue:
            continue
        selected.append(fixture)

    if venue and len(selected) < minimum_sample:
        return summarize(fixtures, team_id, venue=None, minimum_sample=minimum_sample)

    rows: list[tuple[float, float, float, float]] = []
    for fixture in selected:
        corner_pair = team_fixture_stats(fixture, team_id)
        goal_pair = fixture_goals(fixture, team_id)
        if corner_pair is None or goal_pair is None:
            continue
        rows.append((*corner_pair, *goal_pair))

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


def local_kickoff(raw: str, timezone_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return raw.replace("T", " ")[:16]


def report_for_match(
    fixture: dict[str, Any],
    home_summary: Summary,
    away_summary: Summary,
    h2h_summary: Summary | None,
    expected_corners: float,
    expected_goals: float,
    timezone_name: str,
) -> str:
    home_name, away_name = fixture_team_names(fixture)
    kickoff = local_kickoff(str(fixture.get("starting_at", "")), timezone_name)
    h2h_corners = None
    h2h_goals = None
    if h2h_summary:
        h2h_corners = h2h_summary.corners_for + h2h_summary.corners_against
        h2h_goals = h2h_summary.goals_for + h2h_summary.goals_against

    lines = [
        f"🔥 {home_name} – {away_name}",
        f"🗓 {kickoff} ({timezone_name})",
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
    sportmonks_token = os.getenv("SPORTMONKS_TOKEN")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not sportmonks_token or not telegram_token or not telegram_chat_id:
        raise RuntimeError(
            "Set SPORTMONKS_TOKEN, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before running."
        )

    league_ids = [
        int(value.strip())
        for value in os.getenv("LEAGUE_IDS", "271").split(",")
        if value.strip()
    ]
    lookahead_days = env_int("LOOKAHEAD_DAYS", 1)
    history_matches = env_int("HISTORY_MATCHES", 10)
    h2h_matches = env_int("H2H_MATCHES", 5)
    min_sample = env_int("MIN_SAMPLE", 3)
    corners_threshold = env_float("CORNERS_THRESHOLD", 10.0)
    goals_threshold = env_float("GOALS_THRESHOLD", 2.5)
    timezone_name = os.getenv("TIMEZONE", "Europe/Warsaw")
    timezone_info = ZoneInfo(timezone_name)
    today = datetime.now(timezone_info).date()

    api = Sportmonks(sportmonks_token)
    upcoming: dict[int, dict[str, Any]] = {}
    for offset in range(lookahead_days + 1):
        day = today + timedelta(days=offset)
        for fixture in api.upcoming(day, day):
            try:
                league_id = int(fixture.get("league_id", 0))
                fixture_id = int(fixture["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if league_id in league_ids and fixture_is_upcoming(fixture):
                upcoming[fixture_id] = fixture

    if not upcoming:
        send_telegram(telegram_token, telegram_chat_id, "⚽ Brak zaplanowanych meczów Danish Superliga w wybranym okresie.")
        return

    history_by_team: dict[int, list[dict[str, Any]]] = {}
    h2h_by_fixture: dict[int, list[dict[str, Any]]] = {}
    for fixture_id, fixture in upcoming.items():
        home_id, away_id = fixture_team_ids(fixture)
        for team_id in (home_id, away_id):
            if team_id not in history_by_team:
                history_by_team[team_id] = api.team_history(
                    team_id, today, int(fixture.get("league_id", league_ids[0]))
                )[:history_matches]
        league_id = int(fixture.get("league_id", league_ids[0]))
        h2h_by_fixture[fixture_id] = api.head_to_head(home_id, away_id, league_id, h2h_matches)

    selected: list[tuple[float, str]] = []
    failures = 0
    for fixture_id, fixture in upcoming.items():
        home_id, away_id = fixture_team_ids(fixture)
        try:
            home_summary = summarize(
                history_by_team[home_id], home_id, venue="home", minimum_sample=min_sample
            )
            away_summary = summarize(
                history_by_team[away_id], away_id, venue="away", minimum_sample=min_sample
            )
            h2h_summary = summarize(h2h_by_fixture[fixture_id], home_id, minimum_sample=min_sample)
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
                        timezone_name,
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
        message = "📊 NAJCIEKAWSZE MECZE DANISH SUPERLIGA\n\n" + message
    else:
        message = (
            "📊 NAJCIEKAWSZE MECZE DANISH SUPERLIGA\n\n"
            f"Brak meczów spełniających warunki: rożne > {corners_threshold:g} "
            f"i gole > {goals_threshold:g}."
        )
    if failures:
        print(f"Skipped fixtures due to incomplete data: {failures}", file=sys.stderr)
    send_telegram(telegram_token, telegram_chat_id, message[:4090])


if __name__ == "__main__":
    run()
