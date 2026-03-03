# Trading Bot — Shared Workspace

Shared trading intelligence repo for Elio, Ana, and Khanh.

## What's here

- `scanner.py` — Elio's SPX/VIX hourly scanner, breakout detection, paper trading, group chat alerts
- `playbooks/` — trading playbooks and pre-trade checklists
- `scanners/` — individual scanners from each contributor
- `schemas/` — shared Supabase table definitions

## Supabase DB (shared)
- Host: aws-0-us-west-2.pooler.supabase.com:6543
- Tables: hourly_candles, vix_hourly, breakout_signals, market_predictions, paper_trades, paper_account

## Contributing
Add your scanner to `scanners/<your-name>/` and your playbook rules to `playbooks/<your-name>.md`
