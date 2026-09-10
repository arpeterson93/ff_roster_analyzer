"""Thin CLI wrapper over engine.faab_estimate - the estimator logic itself
lives there now (shared with the live pipeline), this just fits it and
prints the regression coefficient table for a quick sanity check after
regenerating o-league-training-table.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.faab_estimate import FaabModel  # noqa: E402

if __name__ == "__main__":
    model = FaabModel()
    print(f"trainable rows: {len(model.rows)}")
    print("season avg team spend:", {k: round(v, 2) for k, v in sorted(model.season_avg_spend.items())})
    print("\nregression coefficients (on log1p(pct of season-avg team spend)):")
    for name, c in model.coefs.items():
        print(f"  {name:28s} {c:+.4f}")
