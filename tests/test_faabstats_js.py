"""docs/js/faabstats.js against a hand-checkable fixture (skipped without node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.faab_league import COLS, normalize_rows

NODE = shutil.which("node")
JS = Path(__file__).resolve().parent.parent / "docs" / "js" / "faabstats.js"


def _row(week, team, pid, signal, bid, pos="RB", typ="WAIVER", season=2025):
    return {"season": season, "week": week, "team_id": team, "type": typ, "signal": signal, "bid_amount_dollars": bid,
            "effective_cost_dollars": bid if signal == "won" else 0.0, "add_player_id": pid, "add_player_name": f"P{pid}",
            "position": pos, "date": week * 10 + team}


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_ratio_auction_and_manager_stats(tmp_path):
    # 40 RB wins at $4 on a $40 budget = 10% each; pooled RB median is 5% -> raw ratio 2.0, shrunk 1 + 1 * 40/50 = 1.8
    rows = [_row(2 + i % 3, 1 + i % 2, 100 + i, "won", 4.0) for i in range(40)]
    rows += [_row(2, 3, 100, "outbid", 3.0)]  # a loser on player 100's week-2 auction
    fl = {
        "cols": COLS, "rows": normalize_rows(rows, {2025: 40.0}), "budgets": {"2025": 40.0}, "season": 2025,
        "week_buckets": [[1, 3], [4, 6]],
        "baselines": {"all": {"n": 100, "p50": 0.05}, "by_position": {"RB": {"n": 100, "p50": 0.05, "p25": 0.02, "p75": 0.1, "p90": 0.2}}, "by_week_bucket": {}, "by_position_week_bucket": {}},
    }
    (tmp_path / "fl.json").write_text(json.dumps(fl))
    (tmp_path / "package.json").write_text('{"type":"module"}')
    (tmp_path / "faabstats.js").write_text(JS.read_text(encoding="utf-8"))
    (tmp_path / "run.mjs").write_text("""
import fs from "fs";
const m = await import("./faabstats.js");
const fl = JSON.parse(fs.readFileSync("fl.json"));
const rows = m.leagueRows(fl), wins = rows.filter(m.isContestedWin);
const r = m.ratioStat(wins, fl.baselines, "RB", null);
const a = m.auctionStats(rows);
const mg = m.managerStats(rows);
console.log(JSON.stringify({ n: r.n, ratio: r.ratio, shrunk: r.shrunk, auctions: a.auctions, contested: a.contestedShare, teams: mg.length, outbid: mg.find(x => x.team_id === 3).outbid }));
""")
    out = subprocess.run([NODE, "run.mjs"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got["n"] == 40 and got["ratio"] == pytest.approx(2.0) and got["shrunk"] == pytest.approx(1.8)
    assert got["auctions"] == 40 and got["teams"] == 3 and got["outbid"] == 1
    # player 100's week-2 auction drew 2 bids (win + one outbid); every other auction drew 1
    assert got["contested"] == pytest.approx(1 / 40)
