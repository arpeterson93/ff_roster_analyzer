from engine.history import WinConfidence, build_history, lineup_efficiency, schedule_swap


def g(season, week, mgr, pf, opp, pa, playoff=False):
    return {"season": season, "week": week, "manager": mgr, "pf": pf, "opp": opp, "pa": pa, "playoff": playoff,
            "result": "W" if pa is None or pf > pa else "L" if pf < pa else "T"}


def week(w, scores, pairs):
    out = []
    for a, b in pairs:
        out.append(g(2020, w, a, scores[a], b, scores[b]))
        out.append(g(2020, w, b, scores[b], a, scores[a]))
    return out


def test_win_confidence_in_sample():
    wc = WinConfidence([60, 80, 100, 120])
    assert wc.of(120) == 1.0
    assert wc.of(60) == 0.0
    assert abs(wc.of(100) - 2 / 3) < 1e-9


def test_schedule_swap_substitutes_rest_of_league_average():
    scores = {"A": 100, "B": 80, "C": 120, "D": 60}
    games = week(1, scores, [("A", "B"), ("C", "D")])
    sw = schedule_swap(games)
    # A on B's schedule: B's opponent is A itself -> rest-of-league avg (80+120+60)/3 = 86.67 < 100 -> win, flagged
    cell = sw["A"]["by_schedule"]["B"]
    assert cell["w"] == 1 and cell["subbed"] == [1]
    # A on C's schedule: faces D (60) -> win, no substitution
    assert sw["A"]["by_schedule"]["C"]["subbed"] == []
    assert sw["A"]["actual"] == 1.0


def test_schedule_swap_none_without_opponents():
    assert schedule_swap([g(2014, 1, "A", 100, None, None)]) is None


def test_lineup_efficiency_uses_bench():
    entries = [
        {"id": 1, "pos": "RB", "slot": "RB", "pts": 5.0},
        {"id": 2, "pos": "RB", "slot": "BE", "pts": 20.0},
    ]
    actual, best = lineup_efficiency(entries, {"RB": 1})
    assert (actual, best) == (5.0, 20.0)


def test_build_history_eras_and_playoffs_excluded_from_power():
    games = []
    for w in (1, 2):
        games += week(w, {"A": 100, "B": 80, "C": 120, "D": 60}, [("A", "B"), ("C", "D")])
    games.append(g(2020, 15, "A", 90, "B", 70, playoff=True))
    games.append(g(2020, 15, "B", 70, "A", 90, playoff=True))
    raw = {"seasons": {"2020": {"team_count": 4, "reg_weeks": 14, "source": "espn"}}, "team_games": games}
    h = build_history(raw, [], {"eras": [{"id": "x", "from": 2019}]})
    assert h["seasons"]["2020"]["power"]["weeks"] == [1, 2]
    assert h["all_time"]["all"][0]["games"] > h["all_time"]["reg"][0]["games"] or len(h["all_time"]["all"]) == 4
    assert h["wc_curves"]["x"]["n"] == 8
