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
    print(f"interest rows (deduped, one per event): {len(model.interest_rows)}")
    print(f"price rows (won only): {len(model.price_rows)}")
    print("\ninterest (logistic, P(anyone bids)) coefficients:")
    for name, c in model.interest_coefs.items():
        print(f"  {name:28s} {c:+.4f}")
    print("\nprice (OLS on log1p(pct of effective starting budget), won rows only) coefficients:")
    for name, c in model.price_coefs.items():
        print(f"  {name:28s} {c:+.4f}")
