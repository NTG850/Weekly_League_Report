import io
import re
from xml.sax.saxutils import escape
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _strip_emojis(text: str) -> str:
    """Strips non-Latin/emoji characters for core PDF font safety."""
    return re.sub(r"[^\x00-\x7F]+", "", text or "").strip()


def build_sleeper_chat_power_rankings(
    league_name: str,
    week: int,
    power_rankings_metrics: list[dict],
    ai_power_rankings: list[dict],
    weekly_takeaway: str,
) -> str:
    """
    Returns clean, paste-ready Sleeper chat text containing ONLY the In-Season
    Power Rankings (1-12) using Sleeper team names and the Weekly Takeaway.
    """
    ai_by_rank = {
        int(item.get("rank", idx + 1)): item for idx, item in enumerate(ai_power_rankings)
    }
    ai_by_team = {
        (item.get("team_name") or "").strip().lower(): item
        for item in ai_power_rankings
    }

    lines = [
        f"{league_name.upper()} — WEEK {week} IN-SEASON POWER RANKINGS",
        "",
    ]

    for m in power_rankings_metrics:
        rk = int(m["power_rank"])
        t_name = m["team_name"]
        mgr = m["manager"]
        rec = m["record_str"]
        delta = m["delta_str"]
        ai_item = ai_by_rank.get(rk) or ai_by_team.get(t_name.lower(), {})
        blurb = (ai_item.get("blurb") or "").strip()

        lines.append(f"#{rk}. {t_name} ({mgr}) — {rec} [{delta}]")
        if blurb:
            lines.append(blurb)
        lines.append("")

    if weekly_takeaway:
        lines.append(f"WEEK {week} TAKEAWAY")
        lines.append(weekly_takeaway.strip())

    return "\n".join(lines).strip()


def build_csv_exports(
    power_rankings_metrics: list[dict],
    dynasty_rows: list[dict],
    playoff_odds_rows: list[dict],
) -> dict[str, bytes]:
    """Builds downloadable CSV byte payloads for the core analytics tables."""
    pr_df = pd.DataFrame(power_rankings_metrics)[
        [
            "power_rank",
            "delta_str",
            "team_name",
            "manager",
            "record_str",
            "h2h_str",
            "med_str",
            "pf",
            "pa",
            "all_play_pct",
            "recent_avg",
            "efficiency_pct",
            "power_index",
        ]
    ]

    dyn_df = pd.DataFrame(dynasty_rows)[
        [
            "dynasty_rank",
            "team_name",
            "manager",
            "tier_tag",
            "total_dynasty_val",
            "roster_val",
            "picks_val",
            "avg_age",
            "picks_summary",
            "bench_breakout_signal",
        ]
    ]

    po_df = pd.DataFrame(playoff_odds_rows)[
        [
            "current_seed",
            "team_name",
            "manager",
            "record",
            "pf",
            "bye_prob",
            "playoff_prob",
            "projected_seed",
            "status",
        ]
    ]

    return {
        "power_rankings": pr_df.to_csv(index=False).encode("utf-8"),
        "dynasty_outlook": dyn_df.to_csv(index=False).encode("utf-8"),
        "playoff_odds": po_df.to_csv(index=False).encode("utf-8"),
    }


def build_readable_pdf_report(
    league_name: str,
    season: str,
    week: int,
    roster_format_summary: str,
    playoff_teams: int,
    bye_teams: int,
    ai_payload: dict,
    power_rankings_metrics: list[dict],
    dynasty_rows: list[dict],
    playoff_odds_rows: list[dict],
    completed_games: list[dict],
) -> bytes:
    """Generates a clean, multi-section PDF report as raw bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "LeagueTitle",
        parent=styles["Heading1"],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        "LeagueSub",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#475569"),
        spaceAfter=12,
    )
    h2_style = ParagraphStyle(
        "SectionH2",
        parent=styles["Heading2"],
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#0284C7"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "CleanBody",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=6,
    )

    story = []
    story.append(
        Paragraph(escape(_strip_emojis(f"{league_name} — Week {week} Report")), title_style)
    )
    story.append(
        Paragraph(
            escape(
                _strip_emojis(
                    f"{season} Season  |  {playoff_teams}-Team Playoffs ({bye_teams} Bye)  |  Slots: {roster_format_summary}"
                )
            ),
            sub_style,
        )
    )

    # 1. Headlines & Superlatives
    story.append(Paragraph("1. Headlines & Superlatives", h2_style))
    for h in ai_payload.get("headlines", []):
        tag = _strip_emojis(h.get("tag", ""))
        htitle = _strip_emojis(h.get("title", ""))
        hbody = _strip_emojis(h.get("body", ""))
        story.append(
            Paragraph(f"<b>[{escape(tag)}] {escape(htitle)}</b>: {escape(hbody)}", body_style)
        )

    # 2. In-Season Power Rankings + Weekly Takeaway
    story.append(Paragraph("2. In-Season Power Rankings", h2_style))
    ai_pr_map = {
        int(item.get("rank", idx + 1)): item
        for idx, item in enumerate(ai_payload.get("power_rankings", []))
    }
    for m in power_rankings_metrics:
        rk = int(m["power_rank"])
        t_name = _strip_emojis(m["team_name"])
        mgr = _strip_emojis(m["manager"])
        rec = _strip_emojis(m["record_str"])
        delta = _strip_emojis(m["delta_str"])
        blurb = _strip_emojis(ai_pr_map.get(rk, {}).get("blurb", ""))
        story.append(
            Paragraph(
                f"<b>#{rk} {escape(t_name)}</b> ({escape(mgr)}, {escape(rec)}, {escape(delta)}) — {escape(blurb)}",
                body_style,
            )
        )

    takeaway = _strip_emojis(ai_payload.get("weekly_takeaway", ""))
    if takeaway:
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"<b>Weekly Takeaway:</b> {escape(takeaway)}", body_style))

    # 3. Dynasty Outlook Table
    story.append(Paragraph("3. Dynasty Outlook & Future Capital", h2_style))
    dyn_table_data = [["Rank", "Team", "Tier", "Dynasty Val", "Age", "Future Picks"]]
    for d in dynasty_rows[:12]:
        dyn_table_data.append(
            [
                str(d["dynasty_rank"]),
                _strip_emojis(d["team_name"]),
                _strip_emojis(d["tier_tag"]),
                f"{d['total_dynasty_val']:,.0f}",
                str(d["avg_age"]),
                _strip_emojis(d["picks_summary"]),
            ]
        )
    dyn_tbl = Table(dyn_table_data, repeatRows=1)
    dyn_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F8FAFC")],
                ),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(dyn_tbl)

    # 4. Playoff & Bye Odds Table
    story.append(Paragraph("4. Playoff Picture & Clinch Odds", h2_style))
    po_table_data = [["Seed", "Team", "Rec", "PF", "#1 Bye %", "Playoff %", "Status"]]
    for p in playoff_odds_rows:
        po_table_data.append(
            [
                str(p["current_seed"]),
                _strip_emojis(p["team_name"]),
                _strip_emojis(p["record"]),
                f"{p['pf']:.1f}",
                f"{p['bye_prob']:.1f}%",
                f"{p['playoff_prob']:.1f}%",
                _strip_emojis(p["status"]),
            ]
        )
    po_tbl = Table(po_table_data, repeatRows=1)
    po_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F8FAFC")],
                ),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(po_tbl)

    # 5. Matchup Recaps & Bench Swings
    story.append(Paragraph("5. Matchup Recaps & Bench-Sit Swings", h2_style))
    for g in completed_games:
        w_team = _strip_emojis(g["winner_team"])
        l_team = _strip_emojis(g["loser_team"])
        swing_txt = _strip_emojis(g["loser_bench_swing"]["verdict"])
        story.append(
            Paragraph(
                f"<b>{escape(w_team)} ({g['winner_pts']}) def. {escape(l_team)} ({g['loser_pts']})</b> — {escape(swing_txt)}",
                body_style,
            )
        )

    doc.build(story)
    return buf.getvalue()