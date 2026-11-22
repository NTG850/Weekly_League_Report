import json
import os
from typing import Literal
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import streamlit as st

GEMINI_MODEL = "gemini-3.8-flash"


class HeadlineCard(BaseModel):
    tag: str = Field(
        description="Short superlative label, e.g., TOP SCORE, BENCH HEARTBREAK, RISER"
    )
    title: str = Field(description="Punchy third-person headline in Austin's style")
    body: str = Field(
        description="2-3 sentences of grounded third-person commentary using Sleeper team names and verified roster players"
    )


class PowerRankingEntry(BaseModel):
    rank: int = Field(description="1 through 12 pure in-season power rank")
    team_name: str = Field(description="Exact Sleeper team name")
    manager: str
    headline_line: str = Field(
        description="Short third-person opener featuring the Sleeper team name"
    )
    blurb: str = Field(
        description=(
            "4-6 sentences of third-person commentary in Austin's voice covering "
            "current season form, All-Play record/scoring efficiency, starting slot fit, and recent NFL context "
            "using ONLY players listed in that team's allowed_roster_players"
        )
    )


class DynastyEntry(BaseModel):
    team_name: str = Field(description="Exact Sleeper team name")
    manager: str
    outlook_tag: str = Field(
        description="e.g., Dynasty Juggernaut, Win-Now Contender, Productive Rebuild, Retooling Core"
    )
    summary: str = Field(
        description=(
            "2-3 third-person sentences on dynasty value, age profile, 2027-2028 picks, and bench breakouts "
            "using ONLY players listed in that team's allowed_roster_players"
        )
    )


class TransactionGrade(BaseModel):
    summary_title: str = Field(
        description="Trade or waiver summary using Sleeper team names"
    )
    win_now_grade: str = Field(
        description="Grade and 1 third-person sentence on playoff/bye impact"
    )
    dynasty_grade: str = Field(
        description="Grade and 1 third-person sentence on long-term dynasty value"
    )


class MatchupRecapNote(BaseModel):
    matchup_id: int
    storyline: str = Field(
        description="2-3 third-person sentences covering result, MVP, and slot-legal bench-sit swing using Sleeper team names"
    )


class MatchupPreviewNote(BaseModel):
    matchup_id: int
    spotlight_badge: str = Field(
        description="e.g., GAME OF THE WEEK, #1 BYE CLASH, #7 BUBBLE BATTLE, or MATCHUP"
    )
    preview_text: str = Field(
        description="2-3 third-person sentences covering win odds, projected scoring, and playoff stakes"
    )


class VideoSceneBeat(BaseModel):
    scene_number: int
    beat_type: Literal[
        "headline", "power_ranking", "bench_swing", "dynasty_or_trade", "preview"
    ]
    target_nfl_player: str = Field(
        description="Primary NFL player or team name to match in the weekly highlight video"
    )
    lower_third_text: str = Field(
        description="Concise third-person on-screen caption (max 12 words) in Austin's voice"
    )


class WeeklyReportPayload(BaseModel):
    headlines: list[HeadlineCard]
    power_rankings: list[PowerRankingEntry]
    weekly_takeaway: str
    dynasty_outlook: list[DynastyEntry]
    transaction_grades: list[TransactionGrade]
    matchup_recaps: list[MatchupRecapNote]
    matchup_previews: list[MatchupPreviewNote]
    video_beats: list[VideoSceneBeat]


def load_austin_style_pack(path: str = "data/austin_style_pack.md") -> str:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "Write in an engaging, witty, direct fantasy football rundown voice with sharp NFL references."


def _get_gemini_api_key() -> str:
    try:
        if "GEMINI_API_KEY" in st.secrets and st.secrets["GEMINI_API_KEY"]:
            return str(st.secrets["GEMINI_API_KEY"]).strip()
    except Exception:
        pass
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _build_roster_ownership_map(snapshot: dict, players_db: dict) -> dict[str, list[dict]]:
    """
    Maps each manager name and Sleeper team name to structured metadata
    (name, pos, nfl_team, age, years_exp) for every player on that roster.
    """
    users_by_id = {
        str(u["user_id"]): u for u in (snapshot.get("users") or []) if u.get("user_id")
    }
    ownership: dict[str, list[dict]] = {}

    for r in snapshot.get("rosters") or []:
        if not isinstance(r, dict) or r.get("roster_id") is None:
            continue
        owner = users_by_id.get(str(r.get("owner_id") or ""), {})
        mgr_name = (owner.get("display_name") or "").strip() or f"Manager {r.get('roster_id')}"
        team_name = (
            ((owner.get("metadata") or {}).get("team_name") or "").strip()
            or mgr_name
        )
        roster_players = []
        seen = set()
        for pid in r.get("players") or []:
            p_info = players_db.get(str(pid)) or {}
            full_name = (
                p_info.get("full_name")
                or f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip()
            )
            if not full_name or full_name in seen:
                continue
            seen.add(full_name)
            years_exp = p_info.get("years_exp")
            roster_players.append(
                {
                    "name": full_name,
                    "pos": (p_info.get("position") or "").upper(),
                    "nfl_team": (p_info.get("team") or "FA").upper(),
                    "age": p_info.get("age"),
                    "years_exp": years_exp,
                    "is_rookie": years_exp == 0,
                }
            )

        roster_players.sort(key=lambda x: x["name"])
        ownership[mgr_name.lower()] = roster_players
        ownership[team_name.lower()] = roster_players

    return ownership


def _attach_allowed_rosters(
    rows: list[dict], ownership_map: dict[str, list[dict]]
) -> list[dict]:
    enriched = []
    for item in rows:
        copy_item = dict(item)
        t_key = (copy_item.get("team_name") or "").strip().lower()
        m_key = (copy_item.get("manager") or "").strip().lower()
        copy_item["allowed_roster_players"] = (
            ownership_map.get(t_key) or ownership_map.get(m_key) or []
        )
        enriched.append(copy_item)
    return enriched


def _format_recent_transactions(snapshot: dict, players_db: dict) -> list[dict]:
    txns = snapshot.get("transactions") or []
    users_by_id = {
        str(u["user_id"]): u for u in (snapshot.get("users") or []) if u.get("user_id")
    }
    roster_to_team = {}
    for r in snapshot.get("rosters") or []:
        if not isinstance(r, dict) or r.get("roster_id") is None:
            continue
        owner = users_by_id.get(str(r.get("owner_id") or ""), {})
        mgr = (owner.get("display_name") or "").strip() or f"Team {r['roster_id']}"
        t_name = ((owner.get("metadata") or {}).get("team_name") or "").strip() or mgr
        roster_to_team[int(r["roster_id"])] = t_name

    cleaned = []
    for t in txns[:20]:
        if not isinstance(t, dict) or t.get("status") != "complete":
            continue
        t_type = t.get("type", "move")
        involved = [
            roster_to_team.get(int(rid), f"Team {rid}")
            for rid in (t.get("roster_ids") or [])
            if rid is not None
        ]
        adds = [
            (players_db.get(str(pid)) or {}).get("full_name", str(pid))
            for pid in (t.get("adds") or {}).keys()
        ]
        drops = [
            (players_db.get(str(pid)) or {}).get("full_name", str(pid))
            for pid in (t.get("drops") or {}).keys()
        ]
        draft_picks = t.get("draft_picks") or []
        cleaned.append(
            {
                "type": t_type,
                "teams": involved,
                "adds": adds,
                "drops": drops,
                "picks": draft_picks,
            }
        )
    return cleaned


def _gather_grounded_nfl_context(
    client: genai.Client,
    season: str,
    week: int,
    completed_games_data: list[dict],
    transactions_summary: list[dict],
) -> str:
    """Pass 1: Focused Google Search grounding for featured matchup & transaction players."""
    featured_players = set()
    for g in completed_games_data:
        for key in ("winner_mvp", "loser_mvp"):
            name = (g.get(key) or {}).get("name")
            if name and name != "N/A":
                featured_players.add(name)
        for sw_key in ("winner_bench_swing", "loser_bench_swing"):
            swap = (g.get(sw_key) or {}).get("best_swap") or {}
            if swap.get("bench_player"):
                featured_players.add(swap["bench_player"])
            if swap.get("replaced_player"):
                featured_players.add(swap["replaced_player"])

    for tx in transactions_summary:
        for p in (tx.get("adds") or []) + (tx.get("drops") or []):
            featured_players.add(p)

    player_list_str = ", ".join(sorted(featured_players)[:20])
    if not player_list_str:
        return "No additional NFL news fetched."

    try:
        search_res = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=(
                f"Check real {season} NFL Season Week {week} and Week {week + 1} injury reports, "
                f"performances, and depth chart notes for these NFL players: {player_list_str}. "
                f"Keep each bullet concise and factual."
            ),
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
            ),
        )
        return search_res.text or "No additional NFL news fetched."
    except Exception:
        return "NFL search grounding skipped."


def generate_weekly_report_ai(
    league_id: str,
    league_name: str,
    season: str,
    week: int,
    roster_format_summary: str,
    scoring_settings: dict,
    playoff_teams: int,
    bye_teams: int,
    power_rankings_data: list[dict],
    completed_games_data: list[dict],
    dynasty_data: list[dict],
    playoff_odds_data: list[dict],
    next_week_previews_data: list[dict],
    transactions_summary: list[dict],
    style_pack_text: str,
    roster_ownership_map: dict[str, list[dict]] | None = None,
) -> dict:
    """
    Two-pass Gemini 3.8 Flash pipeline with strict roster ownership,
    experience/rookie accuracy, and slot-legal bench analysis.
    """
    api_key = _get_gemini_api_key()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. Add it to .streamlit/secrets.toml or your environment."
        )

    client = genai.Client(api_key=api_key)

    nfl_news_notes = _gather_grounded_nfl_context(
        client=client,
        season=season,
        week=week,
        completed_games_data=completed_games_data,
        transactions_summary=transactions_summary,
    )

    ownership_map = roster_ownership_map or {}
    scoped_rankings = _attach_allowed_rosters(power_rankings_data, ownership_map)
    scoped_dynasty = _attach_allowed_rosters(dynasty_data, ownership_map)

    system_instruction = f"""
You are writing the weekly fantasy football report for "{league_name}" ({season} NFL Season, Week {week}).
Channel the punchy, witty, observant personality and dry humor of the style pack below, BUT write strictly in **THIRD PERSON** as an objective league observer.
- NEVER use first-person pronouns ("I", "I'm", "I've", "we", "our", "us", "my", "me") anywhere in any field.
- Always refer to franchises by their exact Sleeper `team_name`, optionally pairing the manager name in parentheses when helpful.
- Do NOT include any commissioner rundown credit or byline tag.
- Use the exact numerical metrics (`record_str`, `pf`, `all_play_record`, `recent_avg`, `efficiency_pct`, `total_dynasty_val`, `avg_age`, `picks_summary`) provided in the JSON payload—never contradict or invent stats.

STRICT ROSTER, POSITION & EXPERIENCE ACCURACY RULES (CRITICAL):
- Every team in `power_rankings` and `dynasty_outlook` includes `allowed_roster_players` with `name`, `pos`, `nfl_team`, `age`, `years_exp`, and `is_rookie`.
- You MUST ONLY mention an NFL player in a team's power rankings blurb or dynasty summary if that player appears in that specific team's `allowed_roster_players` list (or in that team's own matchup/transaction data).
- NEVER call a player a "rookie" or "first-year" player unless `is_rookie` is `true` (`years_exp == 0`). For example, 2024 draftees like Bucky Irving are 3rd-year veterans in {season}, NOT rookies.
- Respect exact starting roster slots ({roster_format_summary}): NEVER suggest starting a QB over an RB/WR/TE or any illegal cross-position swap. Base bench-sit commentary strictly on `winner_bench_swing` and `loser_bench_swing` in `completed_games`.

LEAGUE RULES & ROSTER CONTEXT:
- Format: 12-team Dynasty, {playoff_teams}-team playoff bracket with {bye_teams} first-round bye (#1 seed).
- Starting Roster Slots: {roster_format_summary}.
- Scoring Highlights: {json.dumps(scoring_settings)}

VERIFIED NFL NEWS & INJURY CONTEXT (FROM GOOGLE SEARCH):
{nfl_news_notes}

STYLE & PERSONALITY REFERENCE PACK:
{style_pack_text[:14000]}

STRICT OUTPUT REQUIREMENTS:
- Populate EVERY list in the JSON schema completely using third-person language and exact Sleeper team names:
  * headlines: 4-5 superlative cards.
  * power_rankings: ALL 12 teams ranked 1 to 12 with detailed **4-6 sentence** third-person blurbs in Austin's voice—unpacking current-season record, All-Play record, scoring form, starting slot fit, and verified roster/NFL context.
  * weekly_takeaway: 1 punchy third-person closing paragraph summarizing the week.
  * dynasty_outlook: ALL 12 teams with third-person dynasty summaries (dynasty value, age profile, 2027-2028 picks, bench breakouts strictly from that team's `allowed_roster_players`).
  * transaction_grades: Grade every completed trade or notable waiver move in recent_transactions in third person.
  * matchup_recaps: ALL {len(completed_games_data)} completed matchups with storyline + slot-legal bench-sit swing commentary.
  * matchup_previews: ALL {len(next_week_previews_data)} upcoming matchups highlighting #1 bye and #7 bubble stakes.
  * video_beats: 5 to 6 timed storyboard beats targeting real NFL players from this week's matchups.
"""

    prompt_payload = {
        "week": week,
        "season": season,
        "power_rankings": scoped_rankings,
        "completed_games": completed_games_data,
        "dynasty_outlook": scoped_dynasty,
        "playoff_odds": playoff_odds_data,
        "next_week_previews": next_week_previews_data,
        "recent_transactions": transactions_summary,
    }

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=(
            f"Generate the complete structured weekly report for {league_name} Week {week} "
            f"using this exact league data:\n{json.dumps(prompt_payload, default=str)}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=WeeklyReportPayload,
        ),
    )

    if response.parsed is not None:
        return response.parsed.model_dump()
    return json.loads(response.text)