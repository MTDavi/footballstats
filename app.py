"""Daily football corners and goals report for Telegram.

The script combines two sources: 5DollarFootballAPI for the top five
European leagues and Sportmonks for its free Danish and Scottish coverage.
Both sources are normalized to the same internal fixture shape before the
statistics and Telegram report are calculated.
"""

from __future__ import annotations

import json
import os
import sys
from time import sleep
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BASE_URL = "https://api.5dollarfootballapi.com/v1"
SPORTMONKS_BASE_URL = "https://api.sportmonks.com/v3/football"
DEFAULT_LEAGUE_IDS = [
    4160026622,  # Premier League
    4212821298,  # La Liga
    3405541143,  # Serie A
    686337048,   # Bundesliga
    3614399544,  # Ligue 1
]
FINISHED_STATUSES = {"finished", "ft", "full", "aet", "pens"}
UPCOMING_STATUSES = {"scheduled", "not_started", "upcoming"}
SPORTMONKS_FINISHED_STATE_IDS = {5, 7, 8}
SPORTMONKS_UPCOMING_STATE_IDS = {1}
SPORTMONKS_CORNERS_TYPE_ID = 34
SOURCE_FOOTER = (
    "Źródła danych: https://5dollarfootballapi.com oraz https://www.sportmonks.com"
)


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


class FiveDollarFootballApi:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        for attempt in range(6):
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(body)
                except json.JSONDecodeError:
                    detail = body[:500]
                if exc.code == 429 and attempt < 5:
                    retry_after = number(exc.headers.get("Retry-After"))
                    wait_seconds = max(1, int(retry_after or 60))
                    print(
                        f"5DollarFootballAPI rate limit reached; retrying in {wait_seconds}s.",
                        file=sys.stderr,
                    )
                    sleep(wait_seconds)
                    continue
                raise RuntimeError(
                    f"5DollarFootballAPI request failed ({exc.code}): {detail}"
                ) from exc
            except (URLError, TimeoutError) as exc:
                raise RuntimeError(f"5DollarFootballAPI request failed: {exc}") from exc
        else:
            raise RuntimeError("5DollarFootballAPI request failed after retries")

        if payload.get("success") in {0, False}:
            raise RuntimeError(f"5DollarFootballAPI error: {payload}")
        errors = payload.get("errors") or payload.get("error")
        if errors:
            raise RuntimeError(f"5DollarFootballAPI error: {errors}")
        data = payload.get("data", [])
        if isinstance(data, dict):
            return [data]
        return data

    def fixtures_for_day(
        self, day: date, timezone_name: str, status: str = "scheduled"
    ) -> list[dict[str, Any]]:
        local_zone = ZoneInfo(timezone_name)
        start_local = datetime.combine(day, time.min, tzinfo=local_zone)
        end_local = start_local + timedelta(days=1)
        start_utc = int(start_local.astimezone(timezone.utc).timestamp())
        end_utc = int(end_local.astimezone(timezone.utc).timestamp())
        return self.get(
            "/fixtures",
            {
                "start_time": start_utc,
                "end_time": end_utc,
                "status": status,
                "per_page": 100,
                "order": "asc",
                "lang": "pl",
            },
        )

    def team_history(
        self,
        team_id: int,
        oldest: date,
        newest: date,
    ) -> list[dict[str, Any]]:
        start_utc = int(datetime.combine(oldest, time.min, tzinfo=timezone.utc).timestamp())
        end_utc = int(
            datetime.combine(newest + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp()
        )
        return self.get(
            f"/teams/{team_id}/fixtures",
            {
                "start_time": start_utc,
                "end_time": end_utc,
                "status": "finished",
                "per_page": 50,
                "order": "desc",
                "lang": "pl",
            },
        )


class SportmonksApi:
    """Sportmonks adapter for the free Danish and Scottish leagues."""

    def __init__(self, token: str) -> None:
        self.token = token

    def get(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{SPORTMONKS_BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={"Authorization": self.token, "Accept": "application/json"},
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

    def fixtures_for_day(
        self, day: date, timezone_name: str, status: str = "scheduled"
    ) -> list[dict[str, Any]]:
        del timezone_name, status
        rows = self.get(
            f"/fixtures/between/{day.isoformat()}/{day.isoformat()}",
            {
                "include": "participants;state",
                "per_page": 50,
                "order": "asc",
            },
        )
        league_names = {271: "Danish Superliga", 501: "Scottish Premiership"}
        return [
            normalize_sportmonks_fixture(row, league_id, league_names[league_id])
            for row in rows
            if (league_id := int(row.get("league_id", 0))) in league_names
        ]

    def team_history(
        self, team_id: int, oldest: date, newest: date, league_id: int
    ) -> list[dict[str, Any]]:
        fixtures: list[dict[str, Any]] = []
        window_end = newest
        while window_end >= oldest:
            window_start = max(oldest, window_end - timedelta(days=99))
            rows = self.get(
                f"/fixtures/between/{window_start.isoformat()}/{window_end.isoformat()}/{team_id}",
                {
                    "include": "statistics;scores;participants;state",
                    "filters": f"fixtureStatisticTypes:{SPORTMONKS_CORNERS_TYPE_ID}",
                    "per_page": 50,
                    "order": "desc",
                },
            )
            fixtures.extend(
                normalize_sportmonks_fixture(row, league_id, "Football")
                for row in rows
                if int(row.get("league_id", 0)) == league_id
            )
            window_end = window_start - timedelta(days=1)
        return fixtures

    def head_to_head(
        self, home_id: int, away_id: int, league_id: int, last: int
    ) -> list[dict[str, Any]]:
        rows = self.get(
            f"/fixtures/head-to-head/{home_id}/{away_id}",
            {
                "include": "statistics;scores;participants;state",
                "filters": f"fixtureLeagues:{league_id}",
                "per_page": last,
                "order": "desc",
            },
        )
        league_name = "Danish Superliga" if league_id == 271 else "Scottish Premiership"
        return [
            normalize_sportmonks_fixture(row, league_id, league_name)
            for row in rows[:last]
        ]


def normalize_sportmonks_fixture(
    fixture: dict[str, Any], league_id: int, league_name: str
) -> dict[str, Any]:
    participants = [
        item for item in (fixture.get("participants") or []) if isinstance(item, dict)
    ]
    by_location: dict[str, dict[str, Any]] = {}
    for participant in participants:
        location = (participant.get("meta") or {}).get("location")
        if location in {"home", "away"}:
            by_location[location] = participant
    if "home" not in by_location or "away" not in by_location:
        if len(participants) < 2:
            raise KeyError("Sportmonks fixture has no two participants")
        by_location = {"home": participants[0], "away": participants[1]}

    home_id = int(by_location["home"]["id"])
    away_id = int(by_location["away"]["id"])
    goals_by_location: dict[str, float] = {}
    scores = fixture.get("scores") or []
    preferred = [item for item in scores if item.get("description") == "CURRENT"]
    if not preferred:
        preferred = [
            item
            for item in scores
            if item.get("description") in {"2ND_HALF", "AET", "FT_PEN"}
        ]
    for item in preferred:
        score = item.get("score") or {}
        location = score.get("participant")
        goals = number(score.get("goals"))
        if location in {"home", "away"} and goals is not None:
            goals_by_location[location] = goals

    corners_by_location: dict[str, float] = {}
    corners_by_team: dict[int, float] = {}
    for item in fixture.get("statistics", []) or []:
        if int(item.get("type_id", 0)) != SPORTMONKS_CORNERS_TYPE_ID:
            continue
        data = item.get("data") or {}
        value = number(data.get("value") if isinstance(data, dict) else data)
        if value is None:
            continue
        participant_id = item.get("participant_id")
        if participant_id is not None:
            corners_by_team[int(participant_id)] = value
        location = item.get("location")
        if location in {"home", "away"}:
            corners_by_location[location] = value
    for location, team_id in (("home", home_id), ("away", away_id)):
        if location not in corners_by_location and team_id in corners_by_team:
            corners_by_location[location] = corners_by_team[team_id]

    state_id = int(fixture.get("state_id", 0))
    if state_id in SPORTMONKS_FINISHED_STATE_IDS:
        status = "finished"
    elif state_id in SPORTMONKS_UPCOMING_STATE_IDS:
        status = "scheduled"
    else:
        status = "unknown"

    return {
        "id": fixture["id"],
        "league": {"id": league_id, "name": league_name},
        "teams": {
            "home": {"id": home_id, "name": str(by_location["home"].get("name", "Unknown"))},
            "away": {"id": away_id, "name": str(by_location["away"].get("name", "Unknown"))},
        },
        "kickoff_utc": str(fixture.get("starting_at", "")),
        "status": status,
        "goals": {
            "home": goals_by_location.get("home"),
            "away": goals_by_location.get("away"),
        },
        "corners": {
            "home": corners_by_location.get("home"),
            "away": corners_by_location.get("away"),
        },
    }


@dataclass
class Summary:
    sample: int
    corners_for: float
    corners_against: float
    goals_for: float
    goals_against: float


def fixture_team_ids(fixture: dict[str, Any]) -> tuple[int, int]:
    teams = fixture.get("teams") or {}
    return int(teams["home"]["id"]), int(teams["away"]["id"])


def fixture_team_names(fixture: dict[str, Any]) -> tuple[str, str]:
    teams = fixture.get("teams") or {}
    return str(teams["home"].get("name", "Unknown")), str(teams["away"].get("name", "Unknown"))


def fixture_league_id(fixture: dict[str, Any]) -> int:
    league = fixture.get("league") or {}
    return int(league.get("id", fixture.get("league_id", 0)))


def fixture_league_name(fixture: dict[str, Any]) -> str:
    return str((fixture.get("league") or {}).get("name", "Football"))


def fixture_status(fixture: dict[str, Any]) -> str:
    return str(fixture.get("status", "")).casefold()


def fixture_is_finished(fixture: dict[str, Any]) -> bool:
    return fixture_status(fixture) in FINISHED_STATUSES


def fixture_is_upcoming(fixture: dict[str, Any]) -> bool:
    return fixture_status(fixture) in UPCOMING_STATUSES


def fixture_kickoff(fixture: dict[str, Any]) -> datetime:
    raw = str(fixture.get("kickoff_utc", ""))
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def team_fixture_stats(
    fixture: dict[str, Any], team_id: int
) -> tuple[float, float] | None:
    home_id, away_id = fixture_team_ids(fixture)
    corners = fixture.get("corners") or {}
    home = number(corners.get("home"))
    away = number(corners.get("away"))
    if home is None or away is None:
        return None
    if team_id == home_id:
        return home, away
    if team_id == away_id:
        return away, home
    return None


def fixture_goals(
    fixture: dict[str, Any], team_id: int
) -> tuple[float, float] | None:
    home_id, away_id = fixture_team_ids(fixture)
    goals = fixture.get("goals") or {}
    home = number(goals.get("home"))
    away = number(goals.get("away"))
    if home is None or away is None:
        return None
    if team_id == home_id:
        return home, away
    if team_id == away_id:
        return away, home
    return None


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
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")


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
    kickoff = local_kickoff(str(fixture["kickoff_utc"]), timezone_name)
    h2h_corners = None
    h2h_goals = None
    if h2h_summary:
        h2h_corners = h2h_summary.corners_for + h2h_summary.corners_against
        h2h_goals = h2h_summary.goals_for + h2h_summary.goals_against
    lines = [
        f"🔥 {home_name} – {away_name}",
        f"🏆 {fixture_league_name(fixture)}",
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


def parse_league_ids() -> list[int]:
    raw = os.getenv("LEAGUE_IDS")
    if not raw:
        return DEFAULT_LEAGUE_IDS
    return [int(value.strip()) for value in raw.split(",") if value.strip()]


def run() -> None:
    api_key = os.getenv("FIVEDOLLARFOOTBALL_API_KEY")
    sportmonks_token = os.getenv("SPORTMONKS_TOKEN")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not api_key or not telegram_token or not telegram_chat_id:
        raise RuntimeError(
            "Set FIVEDOLLARFOOTBALL_API_KEY, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before running."
        )

    league_ids = parse_league_ids()
    lookahead_days = env_int("LOOKAHEAD_DAYS", 1)
    history_matches = env_int("HISTORY_MATCHES", 10)
    h2h_matches = env_int("H2H_MATCHES", 5)
    min_sample = env_int("MIN_SAMPLE", 3)
    corners_threshold = env_float("CORNERS_THRESHOLD", 10.0)
    goals_threshold = env_float("GOALS_THRESHOLD", 2.5)
    timezone_name = os.getenv("TIMEZONE", "Europe/Warsaw")
    timezone_info = ZoneInfo(timezone_name)
    test_today = os.getenv("TEST_TODAY", "false").casefold() == "true"
    now_local = datetime.now(timezone_info)
    if os.getenv("GITHUB_EVENT_NAME") == "schedule":
        run_at_local_hour = env_int("RUN_AT_LOCAL_HOUR", 6)
        if now_local.hour != run_at_local_hour:
            print(
                f"Skipping alternate UTC schedule; local time is {now_local:%H:%M}, "
                f"configured report time is {run_at_local_hour:02d}:00 {timezone_name}."
            )
            return
    today = now_local.date()

    api = FiveDollarFootballApi(api_key)
    providers: dict[str, Any] = {"fivedollar": api}
    provider_leagues: dict[str, set[int]] = {
        "fivedollar": set(league_ids),
    }
    if sportmonks_token:
        providers["sportmonks"] = SportmonksApi(sportmonks_token)
        provider_leagues["sportmonks"] = {
            int(value.strip())
            for value in os.getenv("SPORTMONKS_LEAGUE_IDS", "271,501").split(",")
            if value.strip()
        }
    else:
        print(
            "SPORTMONKS_TOKEN not set; skipping Danish and Scottish fixtures.",
            file=sys.stderr,
        )

    upcoming: dict[int, dict[str, Any]] = {}
    for source_name, provider in providers.items():
        for offset in range(lookahead_days + 1):
            day = today + timedelta(days=offset)
            status = "all" if test_today else "scheduled"
            for fixture in provider.fixtures_for_day(day, timezone_name, status=status):
                try:
                    fixture_id = int(fixture["id"])
                    league_id = fixture_league_id(fixture)
                except (KeyError, TypeError, ValueError):
                    continue
                if league_id in provider_leagues[source_name] and (
                    test_today or fixture_is_upcoming(fixture)
                ):
                    fixture["_provider"] = source_name
                    upcoming[fixture_id] = fixture

    if not upcoming:
        message = "📊 NAJCIEKAWSZE MECZE DNIA\n\nBrak zaplanowanych meczów w wybranym okresie."
        send_telegram(telegram_token, telegram_chat_id, f"{message}\n\n{SOURCE_FOOTER}")
        return

    history_by_team: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    h2h_by_fixture: dict[int, list[dict[str, Any]]] = {}
    oldest = today - timedelta(days=90)
    newest = today - timedelta(days=1)
    for fixture_id, fixture in upcoming.items():
        source_name = str(fixture.get("_provider", "fivedollar"))
        provider = providers[source_name]
        home_id, away_id = fixture_team_ids(fixture)
        league_id = fixture_league_id(fixture)
        for team_id in (home_id, away_id):
            cache_key = (source_name, team_id, league_id)
            if cache_key not in history_by_team:
                if source_name == "sportmonks":
                    history = provider.team_history(team_id, today - timedelta(days=370), newest, league_id)
                else:
                    history = provider.team_history(team_id, oldest, newest)
                history_by_team[cache_key] = [
                    item
                    for item in history
                    if fixture_league_id(item) == league_id and fixture_is_finished(item)
                ]

        home_history = history_by_team[(source_name, home_id, league_id)]
        if source_name == "sportmonks":
            h2h_by_fixture[fixture_id] = provider.head_to_head(
                home_id, away_id, league_id, h2h_matches
            )
        else:
            h2h = []
            for item in home_history:
                try:
                    item_home, item_away = fixture_team_ids(item)
                except (KeyError, TypeError, ValueError):
                    continue
                if away_id in {item_home, item_away}:
                    h2h.append(item)
                if len(h2h) >= h2h_matches:
                    break
            h2h_by_fixture[fixture_id] = h2h

    selected: list[tuple[float, str]] = []
    failures = 0
    for fixture_id, fixture in upcoming.items():
        source_name = str(fixture.get("_provider", "fivedollar"))
        home_id, away_id = fixture_team_ids(fixture)
        league_id = fixture_league_id(fixture)
        try:
            history = history_by_team[(source_name, home_id, league_id)][:history_matches]
            away_history = history_by_team[(source_name, away_id, league_id)][:history_matches]
            home_summary = summarize(history, home_id, venue="home", minimum_sample=min_sample)
            away_summary = summarize(away_history, away_id, venue="away", minimum_sample=min_sample)
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
        message = "📊 NAJCIEKAWSZE MECZE DNIA\n\n" + "\n\n".join(
            report for _, report in selected[:max_matches]
        )
    else:
        message = (
            "📊 NAJCIEKAWSZE MECZE DNIA\n\n"
            f"Brak meczów spełniających warunki: rożne > {corners_threshold:g} "
            f"i gole > {goals_threshold:g}."
        )
    if failures:
        print(f"Skipped fixtures due to incomplete data: {failures}", file=sys.stderr)
    send_telegram(telegram_token, telegram_chat_id, f"{message}\n\n{SOURCE_FOOTER}"[:4090])


if __name__ == "__main__":
    run()
