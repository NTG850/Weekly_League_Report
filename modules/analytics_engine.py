import math
import numpy as np

SLOT_ELIGIBILITY = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "FLEX": {"RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "DEF": {"DEF"},
    "K": {"K"},
}


def _safe_float(val, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        out = float(val)
        return 0.0 if math.isnan(out) or math.isinf(out) else out
    except (TypeError, ValueError):
        return default


def _team_label(owner: dict, roster_id: int) -> tuple[str, str]:
    mgr = (owner.get("display_name") or "").strip() or f"Manager {roster_id}"
    t_name = (owner.get("metadata") or {}).get("team_name") or ""
    t_name = str(t_name).strip() or mgr
    return t_name, mgr


def _extract_week_matchups(snapshot: dict, wk: int) -> list:
    for container_key in ("weekly_matchups", "matchups"):
        container = snapshot.get(container_key)
        if isinstance(container, dict):
            if wk in container and isinstance(container[wk], list) and container[wk]:
                return container[wk]
            str_wk = str(wk)
            if (
                str_wk in container
                and isinstance(container[str_wk], list)
                and container[str_wk]
            ):
                return container[str_wk]
    return []


def _extract_entry_points(entry: dict) -> float:
    if entry.get("points") is not None:
        pts = _safe_float(entry.get("points"), -1.0)
        if pts >= 0.0:
            return round(pts, 2)

    starters_pts = entry.get("starters_points")
    if isinstance(starters_pts, list) and starters_pts:
        return round(sum(_safe_float(x) for x in starters_pts), 2)
    if isinstance(starters_pts, dict) and starters_pts:
        return round(sum(_safe_float(x) for x in starters_pts.values()), 2)

    p_pts_map = entry.get("players_points") or {}
    starters = [str(s) for s in (entry.get("starters") or []) if s and str(s) != "0"]
    if starters and isinstance(p_pts_map, dict) and p_pts_map:
        return round(sum(_safe_float(p_pts_map.get(s)) for s in starters), 2)

    return 0.0


def _active_starting_slots(roster_positions: list[str]) -> list[str]:
    return [
        str(s).upper()
        for s in (roster_positions or [])
        if str(s).upper() in SLOT_ELIGIBILITY
    ]


def _compute_slot_legal_optimal_pts(
    all_player_ids: list[str],
    p_pts_map: dict,
    players_db: dict,
    active_slots: list[str],
) -> float:
    """Computes the highest legal lineup score respecting exact Sleeper slot eligibility."""
    candidates = []
    for pid in all_player_ids:
        pos = ((players_db.get(str(pid)) or {}).get("position") or "").upper()
        pts = _safe_float(p_pts_map.get(str(pid)))
        candidates.append((pts, str(pid), pos))
    candidates.sort(key=lambda x: x[0], reverse=True)

    # Fill strict single-position slots first, then flex slots
    ordered_slots = sorted(
        active_slots, key=lambda sl: len(SLOT_ELIGIBILITY.get(sl, set()))
    )
    used_pids = set()
    total_opt = 0.0
    for slot in ordered_slots:
        allowed = SLOT_ELIGIBILITY.get(slot, set())
        for pts, pid, pos in candidates:
            if pid not in used_pids and pos in allowed:
                used_pids.add(pid)
                total_opt += pts
                break
    return round(total_opt, 2)


def compute_season_metrics(snapshot: dict, players_db: dict, target_week: int) -> dict:
    """
    Computes in-season standings, All-Play record, recent 3-week scoring average,
    slot-legal lineup efficiency, slot-legal bench-sit swing analysis, and
    completed matchup recaps through `target_week`.
    """
    users_by_id = {
        str(u["user_id"]): u for u in (snapshot.get("users") or []) if u.get("user_id")
    }
    rosters = snapshot.get("rosters") or []
    active_slots = _active_starting_slots(snapshot.get("roster_positions") or [])

    roster_info = {}
    for r in rosters:
        if not isinstance(r, dict) or r.get("roster_id") is None:
            continue
        rid = int(r["roster_id"])
        owner = users_by_id.get(str(r.get("owner_id") or ""), {})
        t_name, mgr = _team_label(owner, rid)
        roster_info[rid] = {
            "roster_id": rid,
            "team_name": t_name,
            "manager": mgr,
            "players": [str(p) for p in (r.get("players") or [])],
            "starters": [str(s) for s in (r.get("starters") or [])],
            "h2h_w": 0,
            "h2h_l": 0,
            "h2h_t": 0,
            "med_w": 0,
            "med_l": 0,
            "pf": 0.0,
            "pa": 0.0,
            "weekly_pts": [],
            "optimal_pts_list": [],
        }

    completed_games = []
    all_play_wins = {rid: 0 for rid in roster_info}
    all_play_losses = {rid: 0 for rid in roster_info}
    all_play_ties = {rid: 0 for rid in roster_info}

    for wk in range(1, int(target_week) + 1):
        m_list = _extract_week_matchups(snapshot, wk)
        if not m_list:
            continue

        by_mid = {}
        week_scores = {}

        for entry in m_list:
            if not isinstance(entry, dict) or entry.get("roster_id") is None:
                continue
            rid = int(entry["roster_id"])
            if rid not in roster_info:
                continue

            pts = _extract_entry_points(entry)
            roster_info[rid]["pf"] = round(roster_info[rid]["pf"] + pts, 2)
            roster_info[rid]["weekly_pts"].append((wk, pts))
            week_scores[rid] = pts

            p_pts_map = entry.get("players_points") or {}
            p_list = [
                str(p) for p in (entry.get("players") or roster_info[rid]["players"])
            ]
            starters = [
                str(s) for s in (entry.get("starters") or []) if s and str(s) != "0"
            ]
            if starters and isinstance(p_pts_map, dict) and p_pts_map:
                actual_starter_pts = sum(_safe_float(p_pts_map.get(s)) for s in starters)
                if active_slots:
                    optimal_pts = _compute_slot_legal_optimal_pts(
                        p_list, p_pts_map, players_db, active_slots
                    )
                else:
                    sorted_all = sorted(
                        [_safe_float(p_pts_map.get(pid)) for pid in p_list],
                        reverse=True,
                    )
                    optimal_pts = sum(sorted_all[: max(1, len(starters))])
                roster_info[rid]["optimal_pts_list"].append(
                    (actual_starter_pts, optimal_pts)
                )

            mid = entry.get("matchup_id")
            if mid is not None:
                by_mid.setdefault(int(mid), []).append((rid, pts, entry))

        rids_in_wk = list(week_scores.keys())
        for r_a in rids_in_wk:
            for r_b in rids_in_wk:
                if r_a == r_b:
                    continue
                if week_scores[r_a] > week_scores[r_b]:
                    all_play_wins[r_a] += 1
                elif week_scores[r_a] < week_scores[r_b]:
                    all_play_losses[r_a] += 1
                else:
                    all_play_ties[r_a] += 1

        if week_scores:
            vals = sorted(week_scores.values())
            n_v = len(vals)
            median_val = (
                vals[n_v // 2]
                if n_v % 2 == 1
                else (vals[n_v // 2 - 1] + vals[n_v // 2]) / 2.0
            )
            for rid, pts in week_scores.items():
                if pts >= median_val:
                    roster_info[rid]["med_w"] += 1
                else:
                    roster_info[rid]["med_l"] += 1

        for mid, pair in by_mid.items():
            if len(pair) < 2:
                continue
            pair_sorted = sorted(pair, key=lambda x: x[1], reverse=True)
            w_rid, w_pts, w_entry = pair_sorted[0]
            l_rid, l_pts, l_entry = pair_sorted[1]

            if abs(w_pts - l_pts) < 1e-6:
                roster_info[w_rid]["h2h_t"] += 1
                roster_info[l_rid]["h2h_t"] += 1
            else:
                roster_info[w_rid]["h2h_w"] += 1
                roster_info[l_rid]["h2h_l"] += 1
            roster_info[w_rid]["pa"] = round(roster_info[w_rid]["pa"] + l_pts, 2)
            roster_info[l_rid]["pa"] = round(roster_info[l_rid]["pa"] + w_pts, 2)

            if wk == int(target_week):
                w_mvp = _top_player_from_entry(w_entry, players_db)
                l_mvp = _top_player_from_entry(l_entry, players_db)
                w_swing = _bench_swing_summary(w_entry, players_db, active_slots)
                l_swing = _bench_swing_summary(l_entry, players_db, active_slots)
                completed_games.append(
                    {
                        "matchup_id": mid,
                        "winner_rid": w_rid,
                        "winner_team": roster_info[w_rid]["team_name"],
                        "winner_manager": roster_info[w_rid]["manager"],
                        "winner_pts": w_pts,
                        "loser_rid": l_rid,
                        "loser_team": roster_info[l_rid]["team_name"],
                        "loser_manager": roster_info[l_rid]["manager"],
                        "loser_pts": l_pts,
                        "margin": round(w_pts - l_pts, 2),
                        "winner_mvp": w_mvp,
                        "loser_mvp": l_mvp,
                        "winner_bench_swing": w_swing,
                        "loser_bench_swing": l_swing,
                    }
                )

    rows = []
    for rid, info in roster_info.items():
        total_w = info["h2h_w"] + info["med_w"]
        total_l = info["h2h_l"] + info["med_l"]
        total_t = info["h2h_t"]
        rec_str = f"{total_w}-{total_l}" + (f"-{total_t}" if total_t else "")
        h2h_str = f"{info['h2h_w']}-{info['h2h_l']}" + (
            f"-{info['h2h_t']}" if info["h2h_t"] else ""
        )
        med_str = f"{info['med_w']}-{info['med_l']}"

        ap_w = all_play_wins[rid]
        ap_l = all_play_losses[rid]
        ap_t = all_play_ties[rid]
        ap_total = ap_w + ap_l + ap_t
        ap_pct = round((ap_w / ap_total) * 100.0, 1) if ap_total > 0 else 0.0
        ap_rec_str = f"{ap_w}-{ap_l}" + (f"-{ap_t}" if ap_t else "")

        last_3 = [pts for _, pts in info["weekly_pts"][-3:]]
        recent_avg = round(sum(last_3) / len(last_3), 2) if last_3 else 0.0

        tot_act = sum(a for a, _ in info["optimal_pts_list"])
        tot_opt = sum(o for _, o in info["optimal_pts_list"])
        eff_pct = (
            min(100.0, round((tot_act / tot_opt) * 100.0, 1)) if tot_opt > 0 else 100.0
        )

        total_decisions = max(1, total_w + total_l + total_t)
        win_rate = total_w / total_decisions
        power_index = round(
            (win_rate * 45.0)
            + ((ap_pct / 100.0) * 30.0)
            + (min(recent_avg / 200.0, 1.2) * 15.0)
            + ((eff_pct / 100.0) * 10.0),
            2,
        )

        rows.append(
            {
                "roster_id": rid,
                "team_name": info["team_name"],
                "manager": info["manager"],
                "total_wins": total_w,
                "total_losses": total_l,
                "record_str": rec_str,
                "h2h_str": h2h_str,
                "med_str": med_str,
                "pf": round(info["pf"], 2),
                "pa": round(info["pa"], 2),
                "all_play_pct": ap_pct,
                "all_play_record": ap_rec_str,
                "recent_avg": recent_avg,
                "efficiency_pct": eff_pct,
                "power_index": power_index,
                "delta_str": "—",
            }
        )

    rows.sort(
        key=lambda x: (x["power_index"], x["pf"], x["all_play_pct"]), reverse=True
    )
    for idx, r in enumerate(rows, start=1):
        r["power_rank"] = idx

    return {
        "power_rankings": rows,
        "completed_games": completed_games,
    }


def _top_player_from_entry(entry: dict, players_db: dict) -> dict:
    p_pts = entry.get("players_points") or {}
    starters = [str(s) for s in (entry.get("starters") or []) if s and str(s) != "0"]
    best_pid = None
    best_val = -999.0
    for pid in starters:
        val = _safe_float(p_pts.get(pid))
        if val > best_val:
            best_val = val
            best_pid = pid
    if not best_pid:
        return {"name": "N/A", "pts": 0.0}
    p_obj = players_db.get(str(best_pid)) or {}
    name = p_obj.get("full_name") or f"Player {best_pid}"
    return {"name": name, "pts": round(best_val, 2)}


def _bench_swing_summary(
    entry: dict, players_db: dict, active_slots: list[str]
) -> dict:
    """
    Finds the single largest LEGAL starting-slot swap (e.g., QB-for-QB,
    RB-for-RB/FLEX, WR-for-WR/FLEX/REC_FLEX) so illegal cross-position swaps
    like QB over RB are never suggested.
    """
    p_pts = entry.get("players_points") or {}
    raw_starters = [str(s) for s in (entry.get("starters") or [])]
    starters = [s for s in raw_starters if s and s != "0"]
    all_players = [str(p) for p in (entry.get("players") or []) if p and str(p) != "0"]
    starter_set = set(starters)
    bench = [p for p in all_players if p not in starter_set]

    if not starters or not bench:
        return {
            "swing_pts": 0.0,
            "best_swap": None,
            "verdict": "No bench scoring swing available.",
        }

    best_diff = 0.0
    best_pair = None  # (bench_pid, bench_pts, starter_pid, starter_pts, slot_label)

    for idx, s_pid in enumerate(raw_starters):
        if not s_pid or s_pid == "0":
            continue
        s_info = players_db.get(s_pid) or {}
        s_pos = (s_info.get("position") or "").upper()
        s_pts = _safe_float(p_pts.get(s_pid))

        if idx < len(active_slots):
            slot_name = active_slots[idx]
            allowed_positions = SLOT_ELIGIBILITY.get(slot_name, {s_pos})
        else:
            slot_name = s_pos or "SLOT"
            allowed_positions = {s_pos} if s_pos else set()

        for b_pid in bench:
            b_info = players_db.get(b_pid) or {}
            b_pos = (b_info.get("position") or "").upper()
            if allowed_positions and b_pos not in allowed_positions:
                continue
            b_pts = _safe_float(p_pts.get(b_pid))
            diff = round(b_pts - s_pts, 2)
            if diff > best_diff:
                best_diff = diff
                best_pair = (b_pid, b_pts, s_pid, s_pts, slot_name)

    if best_pair is None or best_diff <= 1.0:
        return {
            "swing_pts": 0.0,
            "best_swap": None,
            "verdict": "Starting lineup was optimal across all legal roster slots.",
        }

    b_pid, b_pts, s_pid, s_pts, slot_name = best_pair
    b_obj = players_db.get(b_pid) or {}
    s_obj = players_db.get(s_pid) or {}
    b_name = b_obj.get("full_name") or b_pid
    s_name = s_obj.get("full_name") or s_pid
    b_pos = (b_obj.get("position") or "").upper()
    s_pos = (s_obj.get("position") or "").upper()

    return {
        "swing_pts": best_diff,
        "best_swap": {
            "slot": slot_name,
            "bench_player": b_name,
            "bench_pos": b_pos,
            "bench_pts": b_pts,
            "replaced_player": s_name,
            "replaced_pos": s_pos,
            "replaced_pts": s_pts,
        },
        "verdict": (
            f"Leaving {b_name} ({b_pos}, {b_pts} pts) on the bench over "
            f"{s_name} ({s_pos}, {s_pts} pts) in the {slot_name} slot left "
            f"{best_diff:.2f} points on the table."
        ),
    }


def compute_dynasty_outlook(
    snapshot: dict, market: dict, players_db: dict, season_metrics: dict
) -> list[dict]:
    """
    Baseline dynasty valuation combining consensus roster value and future pick value.
    """
    users_by_id = {
        str(u["user_id"]): u for u in (snapshot.get("users") or []) if u.get("user_id")
    }
    pr_by_rid = {r["roster_id"]: r for r in season_metrics.get("power_rankings", [])}
    val_map = market.get("player_values") or {}
    pick_map = market.get("pick_values") or {}

    dyn_rows = []
    for r in snapshot.get("rosters") or []:
        if not isinstance(r, dict) or r.get("roster_id") is None:
            continue
        rid = int(r["roster_id"])
        owner = users_by_id.get(str(r.get("owner_id") or ""), {})
        t_name, mgr = _team_label(owner, rid)

        player_ids = [str(p) for p in (r.get("players") or [])]
        ages = []
        scored_players = []
        for pid in player_ids:
            p_info = players_db.get(pid) or {}
            age = p_info.get("age")
            if age is not None:
                try:
                    ages.append(float(age))
                except (TypeError, ValueError):
                    pass
            p_name = p_info.get("full_name") or f"Player {pid}"
            pos = p_info.get("position") or ""
            v = _safe_float(val_map.get(p_name.lower()), default=450.0)
            scored_players.append((v, p_name, pos, age))

        scored_players.sort(key=lambda x: x[0], reverse=True)
        roster_val = round(sum(x[0] for x in scored_players), 1)

        picks_val = 0.0
        pick_counts = {"2027 1st": 0, "2027 2nd": 0, "2028 1st": 0, "2028 2nd": 0}
        for pk in snapshot.get("traded_picks") or []:
            if int(pk.get("owner_id") or -1) == rid and str(pk.get("season")) in {
                "2027",
                "2028",
            }:
                rd = int(pk.get("round") or 2)
                yr = str(pk.get("season"))
                if rd == 1:
                    picks_val += _safe_float(pick_map.get(f"{yr} 1st"), 4800.0)
                    label = f"{yr} 1st"
                    if label in pick_counts:
                        pick_counts[label] += 1
                elif rd == 2:
                    picks_val += _safe_float(pick_map.get(f"{yr} 2nd"), 2100.0)
                    label = f"{yr} 2nd"
                    if label in pick_counts:
                        pick_counts[label] += 1

        picks_val = round(picks_val, 1)
        total_val = round(roster_val + picks_val, 1)
        avg_age = round(sum(ages) / len(ages), 1) if ages else 25.5
        cornerstones = [f"{x[1]} ({x[2]})" for x in scored_players[:3]]

        picks_parts = [f"{cnt}x {k}" for k, cnt in pick_counts.items() if cnt > 0]
        picks_summary = ", ".join(picks_parts) if picks_parts else "Standard pick core"

        young_assets = [
            f"{x[1]} ({x[2]}, age {x[3]})"
            for x in scored_players[3:]
            if x[3] is not None and _safe_float(x[3], 99.0) <= 23.5
        ]
        breakout_signal = (
            ", ".join(young_assets[:2])
            if young_assets
            else (
                f"{scored_players[3][1]} ({scored_players[3][2]})"
                if len(scored_players) > 3
                else "Depth developing"
            )
        )

        dyn_rows.append(
            {
                "roster_id": rid,
                "team_name": t_name,
                "manager": mgr,
                "total_dynasty_val": total_val,
                "roster_val": roster_val,
                "picks_val": picks_val,
                "avg_age": avg_age,
                "cornerstones": cornerstones,
                "picks_summary": picks_summary,
                "bench_breakout_signal": breakout_signal,
                "power_rank": pr_by_rid.get(rid, {}).get("power_rank", 6),
            }
        )

    dyn_rows.sort(key=lambda x: x["total_dynasty_val"], reverse=True)
    for idx, d in enumerate(dyn_rows, start=1):
        d["dynasty_rank"] = idx
        if idx <= 3 and d["power_rank"] <= 4:
            d["tier_tag"] = "Dynasty Juggernaut"
        elif d["power_rank"] <= 6:
            d["tier_tag"] = "Win-Now Contender"
        elif d["avg_age"] <= 25.0:
            d["tier_tag"] = "Productive Rebuild"
        else:
            d["tier_tag"] = "Retooling Core"

    return dyn_rows


def simulate_playoffs_and_next_week(
    snapshot: dict, season_metrics: dict, target_week: int, n_sims: int = 2500
) -> dict:
    """
    Runs Monte Carlo simulations for a 7-team playoff bracket with 1 first-round bye
    (#1 seed). Assigns unique 1-12 projected seeds (each number 1-12 used once)
    and labels eliminated teams as 'Eliminated' with no seed number shown.
    """
    rng = np.random.default_rng(42 + int(target_week))
    playoff_teams = int(snapshot.get("playoff_teams") or 7)
    reg_season_end = int(snapshot.get("playoff_week_start") or 15) - 1
    remaining_weeks = max(0, reg_season_end - int(target_week))

    rankings = season_metrics.get("power_rankings") or []
    n_teams = len(rankings)
    if n_teams == 0:
        return {"playoff_odds": [], "next_week_previews": []}

    standings = sorted(
        rankings,
        key=lambda x: (x["total_wins"], x["pf"], x["all_play_pct"]),
        reverse=True,
    )

    means = np.array(
        [max(95.0, _safe_float(r.get("recent_avg"), 135.0)) for r in standings],
        dtype=float,
    )
    stds = np.full(n_teams, 18.5, dtype=float)
    base_wins = np.array(
        [_safe_float(r.get("total_wins"), 0.0) for r in standings], dtype=float
    )
    base_pf = np.array(
        [_safe_float(r.get("pf"), 0.0) for r in standings], dtype=float
    )

    bye_counts = np.zeros(n_teams, dtype=int)
    playoff_counts = np.zeros(n_teams, dtype=int)
    final_ranks_all_sims = np.zeros((n_sims, n_teams), dtype=int)

    for s_idx in range(n_sims):
        sim_wins = base_wins.copy()
        sim_pf = base_pf.copy()

        for _ in range(remaining_weeks):
            scores = np.maximum(65.0, rng.normal(means, stds))
            sim_pf += scores

            median_pts = float(np.median(scores))
            perm = rng.permutation(n_teams)
            for i in range(0, n_teams - 1, 2):
                a = perm[i]
                b = perm[i + 1]
                if scores[a] > scores[b]:
                    sim_wins[a] += 1.0
                elif scores[b] > scores[a]:
                    sim_wins[b] += 1.0
                else:
                    sim_wins[a] += 0.5
                    sim_wins[b] += 0.5

            for t_i in range(n_teams):
                if scores[t_i] >= median_pts:
                    sim_wins[t_i] += 1.0

        order = np.lexsort((-sim_pf, -sim_wins))
        ranks_this_sim = np.empty(n_teams, dtype=int)
        ranks_this_sim[order] = np.arange(1, n_teams + 1)
        final_ranks_all_sims[s_idx] = ranks_this_sim

        bye_counts[order[0]] += 1
        for p_pos in range(min(playoff_teams, n_teams)):
            playoff_counts[order[p_pos]] += 1

    seventh_team_wins = (
        base_wins[min(playoff_teams - 1, n_teams - 1)]
        if n_teams >= playoff_teams
        else 0.0
    )
    max_possible_wins_each = base_wins + (remaining_weeks * 2.0)

    team_order_for_seed = sorted(
        range(n_teams),
        key=lambda i: (
            float(np.mean(final_ranks_all_sims[:, i])),
            -playoff_counts[i],
            -bye_counts[i],
            -base_pf[i],
        ),
    )
    unique_proj_seed_by_idx = {
        team_idx: seed_num
        for seed_num, team_idx in enumerate(team_order_for_seed, start=1)
    }

    playoff_odds = []
    for idx, team in enumerate(standings):
        current_seed = idx + 1
        playoff_prob = round((playoff_counts[idx] / n_sims) * 100.0, 1)
        bye_prob = round((bye_counts[idx] / n_sims) * 100.0, 1)

        mathematically_eliminated = (
            remaining_weeks > 0 and max_possible_wins_each[idx] < seventh_team_wins
        )
        is_eliminated = mathematically_eliminated or playoff_prob <= 0.0
        unique_seed_num = unique_proj_seed_by_idx[idx]

        if is_eliminated:
            proj_seed = "Eliminated"
            status = "ELIMINATED"
        else:
            proj_seed = unique_seed_num

            if playoff_prob >= 99.0:
                status = "CLINCHED / LOCK"
            elif current_seed == 1 and bye_prob >= 50.0:
                status = "#1 BYE DRIVER"
            elif current_seed <= playoff_teams:
                status = "IN POSITION"
            elif playoff_prob >= 15.0:
                status = "ON THE BUBBLE"
            else:
                status = "LONGSHOT"

        playoff_odds.append(
            {
                "current_seed": current_seed,
                "roster_id": team["roster_id"],
                "team_name": team["team_name"],
                "manager": team["manager"],
                "record": team["record_str"],
                "pf": team["pf"],
                "bye_prob": bye_prob,
                "playoff_prob": playoff_prob,
                "projected_seed": proj_seed,
                "projected_seed_sort": unique_seed_num,
                "eliminated": is_eliminated,
                "status": status,
            }
        )

    next_wk = int(target_week) + 1
    next_matchups = _extract_week_matchups(snapshot, next_wk)
    seed_lookup = {p["roster_id"]: p for p in playoff_odds}
    mean_lookup = {standings[i]["roster_id"]: means[i] for i in range(n_teams)}

    by_mid = {}
    for entry in next_matchups:
        mid = entry.get("matchup_id")
        rid = int(entry["roster_id"])
        if mid is not None and rid in seed_lookup:
            by_mid.setdefault(int(mid), []).append(rid)

    previews = []
    for mid, rids in sorted(by_mid.items()):
        if len(rids) < 2:
            continue
        a_rid, b_rid = rids[0], rids[1]
        a_info = seed_lookup[a_rid]
        b_info = seed_lookup[b_rid]
        a_mu = mean_lookup.get(a_rid, 135.0)
        b_mu = mean_lookup.get(b_rid, 135.0)
        diff = a_mu - b_mu
        a_win = 0.5 * (1.0 + math.erf(diff / (26.0 * math.sqrt(2.0))))
        a_pct = round(max(5.0, min(95.0, a_win * 100.0)), 1)
        b_pct = round(100.0 - a_pct, 1)

        previews.append(
            {
                "matchup_id": mid,
                "team_a_seed": a_info["current_seed"],
                "team_a_team": a_info["team_name"],
                "team_a_manager": a_info["manager"],
                "team_a_record": a_info["record"],
                "team_a_win_prob": a_pct,
                "team_a_proj": round(a_mu, 1),
                "team_b_seed": b_info["current_seed"],
                "team_b_team": b_info["team_name"],
                "team_b_manager": b_info["manager"],
                "team_b_record": b_info["record"],
                "team_b_win_prob": b_pct,
                "team_b_proj": round(b_mu, 1),
            }
        )

    return {
        "playoff_odds": playoff_odds,
        "next_week_previews": previews,
    }