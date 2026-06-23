# World Cup Analyzer (Daily GitHub Issue Report)

This repository includes an automated **World Cup Analyzer** that runs every day and publishes a report into GitHub Issues.

## What it does

- Runs daily at **9:00 AM America/New_York (EST/EDT aware)**
- Collects today's World Cup matches (ET)
- Computes:
  - Win/Draw/Lose likelihoods
  - Player likely scorers/assisters (when data is available)
  - Confidence scores for outcomes and player predictions
- Creates or updates an issue named:
  - `World Cup Analyzer Report — YYYY-MM-DD (ET)`

## Data provider

- **API-Football** (`API_FOOTBALL_KEY`)

## Required GitHub Secrets

In repository settings → Secrets and variables → Actions, add:

- `API_FOOTBALL_KEY`

`GITHUB_TOKEN` is provided by Actions automatically.

## Workflow

- File: `.github/workflows/daily-world-cup-analyzer.yml`
- Scheduled hourly (`0 * * * *`) and executes only when local ET time is exactly `09:00`.
  - This avoids DST cron drift and preserves true 9 AM ET behavior year-round.

## Local run (optional)

```bash
python -m pip install -r requirements.txt
export GITHUB_TOKEN=...
export REPO_FULL_NAME=meeshbalas/worldCup
export API_FOOTBALL_KEY=...
export TZ_TARGET=America/New_York
export TARGET_HOUR=9
export TARGET_MINUTE=0
python scripts/world_cup_analyzer.py
```

## Notes

- Current implementation uses a transparent heuristic model for probabilities and confidence.
- You can later replace with a stronger ML model (Elo + xG + lineup/injury features).
- If no matches are found, the issue states that clearly.
