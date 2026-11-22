import requests
import streamlit as st
from modules.analytics_engine import (
    compute_dynasty_outlook,
    compute_season_metrics,
    simulate_playoffs_and_next_week,
)
from modules.exporters import (
    build_csv_exports,
    build_readable_pdf_report,
    build_sleeper_chat_power_rankings,
)
from modules.gemini_writer import (
    _build_roster_ownership_map,
    _format_recent_transactions,
    generate_weekly_report_ai,
    load_austin_style_pack,
)
from modules.sleeper_client import (
    DEFAULT_LEAGUE_ID,
    SLEEPER_BASE,
    fetch_league_snapshot,
    fetch_nfl_players,
    fetch_nfl_state,
    get_dynasty_market_consensus,
)

st.set_page_config(
    page_title="Weekly League Report",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        max-width: 980px;
    }
    .clean-card {
        background-color: #111827;
        border: 1px solid #243044;
        border-radius: 14px;
        padding: 16px 18px;
        margin-bottom: 14px;
    }
    .card-header-row {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
        margin-bottom: 6px;
    }
    .team-title {
        font-size: 1.08rem;
        font-weight: 700;
        color: #F9FAFB;
        line-height: 1.3;
    }
    .mgr-sub {
        font-size: 0.84rem;
        color: #9CA3AF;
        margin-bottom: 8px;
    }
    .stat-strip {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 10px;
    }
    .stat-pill {
        background-color: #1B2538;
        color: #CBD5E1;
        font-size: 0.78rem;
        font-weight: 600;
        padding: 3px 9px;
        border-radius: 6px;
    }
    .badge-pill {
        background-color: #0369A1;
        color: #E0F2FE;
        font-size: 0.72rem;
        font-weight: 700;
        padding: 3px 9px;
        border-radius: 999px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        white-space: nowrap;
    }
    .delta-pill {
        background-color: #172554;
        color: #7DD3FC;
        font-size: 0.8rem;
        font-weight: 700;
        padding: 3px 9px;
        border-radius: 6px;
        white-space: nowrap;
    }
    .body-copy {
        color: #E5E7EB;
        font-size: 0.95rem;
        line-height: 1.55;
        margin: 0;
    }
    .takeaway-card {
        background-color: #0F172A;
        border: 1px solid #38BDF8;
        border-radius: 14px;
        padding: 18px 20px;
        margin-top: 16px;
        margin-bottom: 18px;
    }
    .swing-note {
        background-color: #172033;
        border-left: 3px solid #38BDF8;
        border-radius: 8px;
        padding: 10px 14px;
        margin-top: 10px;
        font-size: 0.88rem;
        color: #DBEAFE;
        line-height: 1.45;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=300, show_spinner="Checking completed league weeks...")
def get_completed_weeks(league_id: str) -> tuple[str, list[int]]:
    league_resp = requests.get(f"{SLEEPER_BASE}/league/{league_id}", timeout=10)
    league_resp.raise_for_status()
    league = league_resp.json()
    league_name = league.get("name", "Dynasty League")
    settings = league.get("settings", {})
    total_rosters = int(league.get("total_rosters") or 12)
    last_scored_leg = int(settings.get("last_scored_leg") or 0)

    try:
        nfl_state = fetch_nfl_state()
        current_nfl_week = int(nfl_state.get("week") or 18)
        season_type = str(nfl_state.get("season_type") or "regular")
    except Exception:
        current_nfl_week = 18
        season_type = "post"

    if last_scored_leg > 0:
        max_candidate = last_scored_leg
    elif season_type == "post":
        max_candidate = 17
    else:
        max_candidate = max(0, current_nfl_week - 1)

    completed = []
    for wk in range(1, max_candidate + 1):
        try:
            m_resp = requests.get(
                f"{SLEEPER_BASE}/league/{league_id}/matchups/{wk}", timeout=10
            )
            if m_resp.status_code != 200:
                continue
            matchups = m_resp.json() or []
            if len(matchups) < total_rosters:
                continue
            if all(float(m.get("points") or 0.0) > 0.0 for m in matchups):
                completed.append(wk)
        except Exception:
            continue

    return league_name, completed


if "pending_week" not in st.session_state:
    st.session_state.pending_week = None

if "active_report_key" not in st.session_state:
    st.session_state.active_report_key = None

if "generated_reports" not in st.session_state:
    st.session_state.generated_reports = {}

with st.sidebar:
    st.header("Report Settings")
    league_id = st.text_input("Sleeper League ID", value=DEFAULT_LEAGUE_ID).strip()
    run_ai = st.toggle("Generate Gemini AI Narrative", value=True)
    refresh_btn = st.button("Refresh / Clear Cache", width="stretch")

if not league_id:
    st.warning("Enter a Sleeper League ID in the sidebar to load your league.")
    st.stop()

if refresh_btn:
    st.session_state.generated_reports = {}
    st.session_state.active_report_key = None
    st.cache_data.clear()

try:
    league_name_preview, completed_weeks = get_completed_weeks(league_id)
except Exception:
    st.error(
        f"Could not load Sleeper League ID `{league_id}`. "
        "Open the sidebar (`>>`) and enter your valid 18-digit Sleeper League ID."
    )
    st.stop()

st.title(f"🏈 {league_name_preview}")

if not completed_weeks:
    st.info("No completed weeks with final scores are available yet for this league.")
    st.stop()

if (
    st.session_state.pending_week is not None
    and st.session_state.pending_week not in completed_weeks
):
    st.session_state.pending_week = None

wk_col1, wk_col2, wk_col3 = st.columns([2, 2, 1])
with wk_col1:
    default_idx = (
        completed_weeks.index(st.session_state.pending_week)
        if st.session_state.pending_week in completed_weeks
        else None
    )
    picked_week = st.selectbox(
        "Select Completed Week",
        options=completed_weeks,
        index=default_idx,
        placeholder="Choose a completed week...",
        format_func=lambda w: f"Week {w} (Completed)",
        key="main_week_select",
    )
with wk_col2:
    st.caption(
        f"Completed weeks available: Weeks {completed_weeks[0]}–{completed_weeks[-1]}. "
        "Select a week, then press Generate Report."
    )
with wk_col3:
    generate_btn = st.button("Generate Report", type="primary", width="stretch")

st.session_state.pending_week = int(picked_week) if picked_week is not None else None

if generate_btn:
    if picked_week is None:
        st.warning(
            "Choose a completed week from the dropdown first, then click **Generate Report**."
        )
    else:
        chosen_wk = int(picked_week)
        build_key = f"{league_id}::wk{chosen_wk}::ai{int(run_ai)}"

        with st.status(
            f"Generating Week {chosen_wk} Report...", expanded=True
        ) as loader:
            loader.write("Fetching Sleeper snapshot & player pool...")
            players_db = fetch_nfl_players()
            snapshot = fetch_league_snapshot(league_id, chosen_wk)
            market = get_dynasty_market_consensus(snapshot)

            loader.write("Computing standings, lineups & dynasty metrics...")
            season_metrics = compute_season_metrics(snapshot, players_db, chosen_wk)
            dynasty_rows = compute_dynasty_outlook(
                snapshot, market, players_db, season_metrics
            )

            loader.write("Simulating 2,500 playoff & bye scenarios...")
            sim_results = simulate_playoffs_and_next_week(
                snapshot, season_metrics, chosen_wk, n_sims=2500
            )

            ai_payload = {
                "headlines": [],
                "power_rankings": [],
                "weekly_takeaway": "",
                "dynasty_outlook": [],
                "transaction_grades": [],
                "matchup_recaps": [],
                "matchup_previews": [],
                "video_beats": [],
            }

            if run_ai:
                loader.write("Generating grounded report narrative...")
                try:
                    style_pack = load_austin_style_pack()
                    txns_clean = _format_recent_transactions(snapshot, players_db)
                    ownership_map = _build_roster_ownership_map(snapshot, players_db)
                    ai_payload = generate_weekly_report_ai(
                        league_id=league_id,
                        league_name=snapshot["league_name"],
                        season=snapshot["season"],
                        week=chosen_wk,
                        roster_format_summary=snapshot["roster_format"]["summary_text"],
                        scoring_settings=snapshot["scoring_settings"],
                        playoff_teams=snapshot["playoff_teams"],
                        bye_teams=snapshot["bye_teams"],
                        power_rankings_data=season_metrics["power_rankings"],
                        completed_games_data=season_metrics["completed_games"],
                        dynasty_data=dynasty_rows,
                        playoff_odds_data=sim_results["playoff_odds"],
                        next_week_previews_data=sim_results["next_week_previews"],
                        transactions_summary=txns_clean,
                        style_pack_text=style_pack,
                        roster_ownership_map=ownership_map,
                    )
                except Exception as exc:
                    st.error(f"Gemini narrative generation note: {exc}")

            loader.update(
                label=f"Week {chosen_wk} Report Ready",
                state="complete",
                expanded=False,
            )

        st.session_state.generated_reports[build_key] = {
            "target_week": chosen_wk,
            "snapshot": snapshot,
            "market": market,
            "season_metrics": season_metrics,
            "dynasty_rows": dynasty_rows,
            "sim_results": sim_results,
            "ai_payload": ai_payload,
        }
        st.session_state.active_report_key = build_key

active_key = st.session_state.active_report_key
if not active_key or active_key not in st.session_state.generated_reports:
    st.info(
        "No report generated yet. Select a completed week above and click **Generate Report**."
    )
    st.stop()

bundle = st.session_state.generated_reports[active_key]
target_week = int(bundle["target_week"])
snapshot = bundle["snapshot"]
market = bundle["market"]
season_metrics = bundle["season_metrics"]
dynasty_rows = bundle["dynasty_rows"]
sim_results = bundle["sim_results"]
ai_payload = bundle["ai_payload"]

league_name = snapshot["league_name"]
season_str = snapshot["season"]
roster_fmt_summary = snapshot["roster_format"]["summary_text"]
playoff_teams = snapshot["playoff_teams"]
bye_teams = snapshot["bye_teams"]

st.caption(
    f"Viewing Generated Week {target_week} Report • {season_str} Season • "
    f"{playoff_teams}-Team Playoffs ({bye_teams} Bye) • Slots: {roster_fmt_summary} • "
    f"Market: {', '.join(market['sources_active']) or 'Fallback'}"
)

nav_section = st.segmented_control(
    "Section",
    options=[
        "Headlines",
        "Power Rankings",
        "Dynasty",
        "Playoff Odds",
        "Recaps",
        "Previews",
        "Trades & Moves",
        "Exports",
    ],
    default="Headlines",
    wrap=False,
    width="stretch",
)

# 1. HEADLINES & SUPERLATIVES
if nav_section == "Headlines":
    st.subheader(f"Week {target_week} Headlines & Superlatives")
    headlines = ai_payload.get("headlines") or []
    for h in headlines:
        st.markdown(
            f"""
            <div class="clean-card">
                <div class="card-header-row">
                    <div class="team-title">{h.get("title", "")}</div>
                    <span class="badge-pill">{h.get("tag", "HIGHLIGHT")}</span>
                </div>
                <p class="body-copy">{h.get("body", "")}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

# 2. IN-SEASON POWER RANKINGS + WEEKLY TAKEAWAY + SLEEPER CHAT COPY BOX
elif nav_section == "Power Rankings":
    st.subheader(f"Week {target_week} In-Season Power Rankings")
    ai_pr_by_rank = {
        int(x.get("rank", idx + 1)): x
        for idx, x in enumerate(ai_payload.get("power_rankings") or [])
    }

    for row in season_metrics["power_rankings"]:
        rk = row["power_rank"]
        ai_item = ai_pr_by_rank.get(rk, {})
        team_display = row["team_name"]
        mgr_display = row["manager"]
        opener = ai_item.get("headline_line") or ""
        blurb = ai_item.get("blurb") or ""
        ap_disp = row.get("all_play_record") or f"{row['all_play_pct']}%"

        st.markdown(
            f"""
            <div class="clean-card">
                <div class="card-header-row">
                    <div>
                        <div class="team-title">#{rk} {team_display}</div>
                        <div class="mgr-sub">{mgr_display} • Record: {row["record_str"]}</div>
                    </div>
                    <span class="delta-pill">{row["delta_str"]}</span>
                </div>
                <div class="stat-strip">
                    <span class="stat-pill">PF: {row["pf"]}</span>
                    <span class="stat-pill">All-Play: {ap_disp}</span>
                    <span class="stat-pill">L3 Avg: {row["recent_avg"]}</span>
                    <span class="stat-pill">Lineup Eff: {row["efficiency_pct"]}%</span>
                </div>
                {f'<div style="font-weight:600; color:#7DD3FC; margin-bottom:4px; font-size:0.92rem;">{opener}</div>' if opener else ''}
                <p class="body-copy">{blurb}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    takeaway_text = ai_payload.get("weekly_takeaway") or ""
    if takeaway_text:
        st.markdown(
            f"""
            <div class="takeaway-card">
                <div style="font-size:0.78rem; font-weight:700; color:#38BDF8; letter-spacing:0.05em; margin-bottom:6px;">
                    WEEK {target_week} TAKEAWAY
                </div>
                <p class="body-copy">{takeaway_text}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("### Copy Power Rankings for Sleeper Chat")
    sleeper_copy_text = build_sleeper_chat_power_rankings(
        league_name=league_name,
        week=target_week,
        power_rankings_metrics=season_metrics["power_rankings"],
        ai_power_rankings=ai_payload.get("power_rankings") or [],
        weekly_takeaway=takeaway_text,
    )
    st.code(sleeper_copy_text, language=None)

# 3. DYNASTY OUTLOOK & FUTURE CAPITAL
elif nav_section == "Dynasty":
    st.subheader("Dynasty Outlook & Future Capital")
    ai_dyn_by_team = {
        (x.get("team_name") or "").strip().lower(): x
        for x in (ai_payload.get("dynasty_outlook") or [])
    }
    ai_dyn_by_mgr = {
        (x.get("manager") or "").strip().lower(): x
        for x in (ai_payload.get("dynasty_outlook") or [])
    }

    for d in dynasty_rows:
        ai_d = ai_dyn_by_team.get(d["team_name"].lower()) or ai_dyn_by_mgr.get(
            d["manager"].lower(), {}
        )
        tier = ai_d.get("outlook_tag") or d["tier_tag"]
        summary = ai_d.get("summary") or ""

        st.markdown(
            f"""
            <div class="clean-card">
                <div class="card-header-row">
                    <div>
                        <div class="team-title">#{d["dynasty_rank"]} {d["team_name"]}</div>
                        <div class="mgr-sub">{d["manager"]}</div>
                    </div>
                    <span class="badge-pill">{tier}</span>
                </div>
                <div class="stat-strip">
                    <span class="stat-pill">Dynasty Val: {d["total_dynasty_val"]:,.0f}</span>
                    <span class="stat-pill">Avg Age: {d["avg_age"]}</span>
                    <span class="stat-pill">Picks: {d["picks_summary"]}</span>
                </div>
                <p class="body-copy">{summary}</p>
                <div class="swing-note">
                    <strong>Bench Breakout Signal:</strong> {d["bench_breakout_signal"]}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# 4. PLAYOFF PICTURE & CLINCH ODDS
elif nav_section == "Playoff Odds":
    st.subheader(f"Playoff Picture & Clinch Odds ({playoff_teams} Teams / {bye_teams} Bye)")
    po_df_view = []
    for p in sim_results["playoff_odds"]:
        playoff_pct = float(p.get("playoff_prob") or 0.0)
        is_elim = bool(p.get("eliminated")) or playoff_pct <= 0.0
        if is_elim:
            proj_seed_str = "Eliminated"
            status_str = "ELIMINATED"
        else:
            raw_proj = p.get("projected_seed", p.get("current_seed", 1))
            proj_seed_str = str(max(1, min(12, int(round(float(raw_proj))))))
            status_str = str(p.get("status") or "")

        po_df_view.append(
            {
                "Seed": p["current_seed"],
                "Team": p["team_name"],
                "Manager": p["manager"],
                "Rec": p["record"],
                "PF": p["pf"],
                "#1 Bye %": f"{p['bye_prob']:.1f}%",
                "Playoff %": f"{playoff_pct:.1f}%",
                "Proj Seed": proj_seed_str,
                "Status": status_str,
            }
        )
    st.dataframe(po_df_view, width="stretch", hide_index=True)

# 5. THIS WEEK'S MATCHUP RECAPS + BENCH-SIT SWING CHECKS
elif nav_section == "Recaps":
    st.subheader(f"Week {target_week} Matchup Recaps")
    ai_recap_map = {
        int(x.get("matchup_id", -1)): x.get("storyline", "")
        for x in (ai_payload.get("matchup_recaps") or [])
    }

    for g in season_metrics["completed_games"]:
        mid = g["matchup_id"]
        story = ai_recap_map.get(mid, "")
        w_mvp = g["winner_mvp"]
        l_mvp = g["loser_mvp"]
        l_swing = g["loser_bench_swing"]["verdict"]
        w_swing = g["winner_bench_swing"]["verdict"]

        st.markdown(
            f"""
            <div class="clean-card">
                <div class="team-title">{g["winner_team"]} ({g["winner_pts"]}) def. {g["loser_team"]} ({g["loser_pts"]})</div>
                <div class="mgr-sub">{g["winner_manager"]} vs. {g["loser_manager"]}</div>
                <div class="stat-strip">
                    <span class="stat-pill">Top Scorer: {w_mvp["name"]} ({w_mvp["pts"]})</span>
                    <span class="stat-pill">Opp Top: {l_mvp["name"]} ({l_mvp["pts"]})</span>
                    <span class="stat-pill">Margin: +{g["margin"]}</span>
                </div>
                <p class="body-copy">{story}</p>
                <div class="swing-note">
                    <strong>Bench-Sit Swing ({g["loser_team"]}):</strong> {l_swing}<br/>
                    <strong>Bench Note ({g["winner_team"]}):</strong> {w_swing}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# 6. NEXT WEEK'S MATCHUP PREVIEWS
elif nav_section == "Previews":
    next_wk = target_week + 1
    st.subheader(f"Week {next_wk} Matchup Previews")
    ai_prev_map = {
        int(x.get("matchup_id", -1)): x
        for x in (ai_payload.get("matchup_previews") or [])
    }

    previews = sim_results["next_week_previews"]
    if not previews:
        st.info(f"Week {next_wk} matchups are not posted yet on Sleeper.")
    else:
        for p in previews:
            mid = p["matchup_id"]
            ai_p = ai_prev_map.get(mid, {})
            badge = ai_p.get("spotlight_badge") or "MATCHUP"
            ptext = ai_p.get("preview_text") or ""

            st.markdown(
                f"""
                <div class="clean-card">
                    <div class="card-header-row">
                        <div class="team-title">#{p["team_a_seed"]} {p["team_a_team"]} vs. #{p["team_b_seed"]} {p["team_b_team"]}</div>
                        <span class="badge-pill">{badge}</span>
                    </div>
                    <div class="mgr-sub">{p["team_a_manager"]} ({p["team_a_record"]}) vs. {p["team_b_manager"]} ({p["team_b_record"]})</div>
                    <div class="stat-strip">
                        <span class="stat-pill">Win Odds: {p["team_a_win_prob"]}% vs {p["team_b_win_prob"]}%</span>
                        <span class="stat-pill">Proj: {p["team_a_proj"]} vs {p["team_b_proj"]}</span>
                    </div>
                    <p class="body-copy">{ptext}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

# 7. TRADES & MOVES
elif nav_section == "Trades & Moves":
    st.subheader(f"Week {target_week} Trade & Waiver Grades")
    grades = ai_payload.get("transaction_grades") or []
    if not grades:
        st.info("No graded trades or notable moves recorded for this week.")
    else:
        for tg in grades:
            st.markdown(
                f"""
                <div class="clean-card">
                    <div class="team-title" style="margin-bottom:8px;">{tg.get("summary_title", "")}</div>
                    <p class="body-copy" style="margin-bottom:6px;">
                        <strong style="color:#38BDF8;">Win-Now:</strong> {tg.get("win_now_grade", "")}
                    </p>
                    <p class="body-copy">
                        <strong style="color:#93C5FD;">Dynasty:</strong> {tg.get("dynasty_grade", "")}
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )

# 8. EXPORTS
elif nav_section == "Exports":
    st.subheader("Exports")

    csv_files = build_csv_exports(
        season_metrics["power_rankings"],
        dynasty_rows,
        sim_results["playoff_odds"],
    )
    pdf_bytes = build_readable_pdf_report(
        league_name=league_name,
        season=season_str,
        week=target_week,
        roster_format_summary=roster_fmt_summary,
        playoff_teams=playoff_teams,
        bye_teams=bye_teams,
        ai_payload=ai_payload,
        power_rankings_metrics=season_metrics["power_rankings"],
        dynasty_rows=dynasty_rows,
        playoff_odds_rows=sim_results["playoff_odds"],
        completed_games=season_metrics["completed_games"],
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.download_button(
            "Power Rankings CSV",
            data=csv_files["power_rankings"],
            file_name=f"week_{target_week}_power_rankings.csv",
            mime="text/csv",
            width="stretch",
        )
    with c2:
        st.download_button(
            "Dynasty Outlook CSV",
            data=csv_files["dynasty_outlook"],
            file_name=f"week_{target_week}_dynasty_outlook.csv",
            mime="text/csv",
            width="stretch",
        )
    with c3:
        st.download_button(
            "Playoff Odds CSV",
            data=csv_files["playoff_odds"],
            file_name=f"week_{target_week}_playoff_odds.csv",
            mime="text/csv",
            width="stretch",
        )
    with c4:
        st.download_button(
            "Readable PDF Report",
            data=pdf_bytes,
            file_name=f"week_{target_week}_league_report.pdf",
            mime="application/pdf",
            width="stretch",
        )