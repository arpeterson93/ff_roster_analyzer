# Fantasy points allowed by position

Which NFL defenses are the best and worst matchups for QB / RB / WR / TE / K / D/ST this season? This scores every player's real weekly stat line (and each team's D/ST line) from [nflverse](https://github.com/nflverse) and adds it up by the team they faced.

No accounts, logins, or API keys. One file, two small dependencies. There is also a ready-to-run Google Colab version (`fpa_by_position.ipynb`).

## Run it

You need [Python 3.10+](https://www.python.org/downloads/).

```
pip install nflreadpy polars
python fpa_by_position.py
```

| Flag | What it does |
| --- | --- |
| `--ppr 0` / `0.5` / `1` | ESPN default template: standard, half PPR (default), or full PPR |
| `--league-id 123456` | Use **your** public ESPN league's exact scoring (offense, K, D/ST) |
| `--set PY=0.05 --set REC=1` | Override any scoring item (ESPN abbreviations, repeatable) |
| `--show-scoring` | Print the scoring in use and exit |
| `--season 2025` | Season to analyze (default: current/most recent) |
| `--positions QB,WR,DST` | Only these positions (`QB,RB,WR,TE,K,DST`) |
| `--last 4` | Also show each team's average over its last 4 games |
| `--chart DET:WR` | Save a weekly line chart for one team and position (needs `pip install matplotlib`) |
| `--out results` | Write `fpa_summary.csv` / `fpa_weekly.csv` (and the chart) to `./results` |

## Reading the output

```
=== WR: fantasy points allowed (sorted by season +/- Rk; 1 = best matchup) ===
Defense  G     FPA   FPA/G RawRk  OppAvg     +/- +/-Rk  |   L3/G  L3Opp  L3+/-  L3Rk
BUF      6   212.9   35.48     1   25.14   +10.3     1  |   38.87   24.32  +14.5     1
```

- **FPA / FPA/G**: total and per-game fantasy points that team allowed to the position (regular season only; bye weeks don't count). **Raw Rk** ranks teams by FPA/G alone (1 = most allowed), before adjusting for opponents.
- **Opp Avg**: what the average opponent that team faced scores at the position in its *other* games.
- **+/-**: points allowed minus Opp Avg, averaged: how much more (+) or less (-) the team gives up than those offenses normally score. `+3.2` = 3.2 more per game than expected. **+/-Rk** ranks it (1 = best matchup); the HTML report sorts by it by default.
- **L3 columns** (with `--last 3`): the same FPA/G, Opp Avg, +/- and rank for just each team's last 3 games, so you get one rank for the whole season and one for recent form.
- **D/ST** is rated from the other side: the team listed is the *offense*, and the number is how many D/ST points opposing defenses scored against it.
- **Chart**: blue = points the team allowed each week; orange dashed = the average that week's opponent scores at the position in its other games.

## Scoring

Priority: your `--set` overrides, then your `--league-id` (read live from ESPN; private leagues can't be read and the script never asks for login cookies), else ESPN's typical default template for `--ppr`: the most common value of each item across ~900 public ESPN leagues. Run `--show-scoring` to see every item and its ESPN abbreviation. nflverse has no data for a couple of rare items (2-point returns, 1-point safeties); those are skipped and listed.

## Notes

- Data refreshes as nflverse publishes it (usually within a day of each game); the first run downloads the season, later runs are cached.
- Team abbreviations are nflverse's (e.g. `LA` for the Rams).
- Stats are from [nflverse](https://github.com/nflverse); please credit them if you share results.
