import time
import requests
import streamlit as st

SLEEPER_BASE = "https://api.sleeper.app/v1"
DEFAULT_LEAGUE_ID = "1311999384551960576"


def _safe_get_json(url: str, timeout: int = 12, default=None):
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return default
        return resp.json()
    except Exception:
        return default


@st.cache_data(ttl=3600, show_spinner="Loading NFL player database...")
def fetch_nfl_players() -> dict:
    """Fetches and caches the Sleeper NFL players dictionary."""
    data = _safe_get_json(f"{SLEEPER_BASE}/players/nfl", timeout=25, default={})
    return data if isinstance(data, dict) else {}


@st.cache_data(ttl=300, show_spinner="Checking NFL season state...")
def fetch_nfl_state() -> dict:
    """Fetches current NFL state from Sleeper."""
    data = _safe_get_json(f"{SLEEPER_BASE}/state/nfl", timeout=10, default={})
    return data if isinstance(data, dict) else {}


def _format_roster_slots(roster_positions: list[str]) -> str:
    if not roster_positions:
        return "Standard Dynasty Slots"
    order = ["QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "REC_FLEX", "DEF", "K", "BN"]
    counts: dict[str, int] = {}
    for slot in roster_positions:
        s = str(slot or "").strip().upper()
        if s:
            counts[s] = counts.get(s, 0) + 1

    parts = []
    seen = set()
    for s in order:
        if s in counts:
            parts.append(f"{counts[s]} {s}")
            seen.add(s)
    for s, cnt in counts.items():
        if s not in seen:
            parts.append(f"{cnt} {s}")
    return ", ".join(parts) if parts else "Standard Dynasty Slots"


@st.cache_data(ttl=300, show_spinner="Loading Sleeper league snapshot...")
def fetch_league_snapshot(league_id: str, target_week: int) -> dict:
    """
    Fetches league settings, users, rosters, traded picks, transactions across
    weeks 1..target_week, and weekly matchups across weeks 1..max_wk.
    """
    lid = str(league_id).strip()
    league = _safe_get_json(f"{SLEEPER_BASE}/league/{lid}", timeout=12, default={}) or {}
    users = _safe_get_json(f"{SLEEPER_BASE}/league/{lid}/users", timeout=12, default=[]) or []
    rosters = _safe_get_json(f"{SLEEPER_BASE}/league/{lid}/rosters", timeout=12, default=[]) or []
    traded_picks = (
        _safe_get_json(f"{SLEEPER_BASE}/league/{lid}/traded_picks", timeout=12, default=[])
        or []
    )

    league_name = str(league.get("name") or "Dynasty League").strip()
    season = str(league.get("season") or "2026").strip()
    settings = league.get("settings") or {}
    scoring_settings = league.get("scoring_settings") or {}
    roster_positions = league.get("roster_positions") or []

    playoff_teams = int(settings.get("playoff_teams") or 7)
    playoff_week_start = int(settings.get("playoff_week_start") or 15)
    bye_teams = 1 if playoff_teams in (7, 14) else max(0, (2 ** (playoff_teams - 1).bit_length()) - playoff_teams)

    max_wk = max(int(target_week) + 1, int(settings.get("last_scored_leg") or int(target_week)))
    weekly_matchups: dict[int, list[dict]] = {}
    for wk in range(1, max_wk + 1):
        m_data = _safe_get_json(
            f"{SLEEPER_BASE}/league/{lid}/matchups/{wk}", timeout=12, default=[]
        )
        weekly_matchups[wk] = m_data if isinstance(m_data, list) else []

    transactions: list[dict] = []
    for wk in range(1, int(target_week) + 1):
        tx_data = _safe_get_json(
            f"{SLEEPER_BASE}/league/{lid}/transactions/{wk}", timeout=12, default=[]
        )
        if isinstance(tx_data, list):
            transactions.extend(tx_data)

    # Sort newest transactions first when timestamps are present
    transactions.sort(key=lambda x: int(x.get("status_updated") or x.get("created") or 0), reverse=True)

    return {
        "league_id": lid,
        "league_name": league_name,
        "season": season,
        "settings": settings,
        "scoring_settings": scoring_settings,
        "roster_positions": roster_positions,
        "roster_format": {
            "slots": roster_positions,
            "summary_text": _format_roster_slots(roster_positions),
        },
        "playoff_teams": playoff_teams,
        "bye_teams": bye_teams,
        "playoff_week_start": playoff_week_start,
        "users": users if isinstance(users, list) else [],
        "rosters": rosters if isinstance(rosters, list) else [],
        "traded_picks": traded_picks if isinstance(traded_picks, list) else [],
        "weekly_matchups": weekly_matchups,
        "transactions": transactions,
    }


@st.cache_data(ttl=1800, show_spinner="Loading dynasty market values...")
def get_dynasty_market_consensus(_snapshot: dict) -> dict:
    """
    Pulls consensus dynasty player values from FantasyCalc (1QB dynasty format)
    with clean fallback defaults if external market endpoints are unreachable.
    """
    sources_active: list[str] = []
    player_values: dict[str, float] = {}

    fc_data = _safe_get_json(
        "https://api.fantasycalc.com/values/current?isDynasty=true&numQbs=1&numTeams=12&ppr=1",
        timeout=12,
        default=None,
    )
    if isinstance(fc_data, list) and fc_data:
        sources_active.append("FantasyCalc")
        for item in fc_data:
            if not isinstance(item, dict):
                continue
            p_obj = item.get("player") or {}
            full_name = str(p_obj.get("name") or "").strip().lower()
            val = item.get("value")
            if full_name and val is not None:
                try:
                    player_values[full_name] = float(val)
                except (TypeError, ValueError):
                    continue

    pick_values = {
        "2027 1st": 5200.0,
        "2027 2nd": 2200.0,
        "2028 1st": 4500.0,
        "2028 2nd": 1850.0,
    }

    return {
        "sources_active": sources_active,
        "player_values": player_values,
        "pick_values": pick_values,
        "fetched_at": int(time.time()),
    }