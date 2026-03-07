module.exports = {
  apps: [
    {
      name: "trading-scanner",
      script: "/Library/Developer/CommandLineTools/usr/bin/python3",
      args: "/Users/elio/Projects/trading-bot/scanner.py",
      cwd: "/Users/elio/Projects/trading-bot",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
    {
      name: "paper-trader",
      script: "scripts/run_paper_trading.py",
      interpreter: "python3",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
    {
      name: "candle-collector",
      script: "services/candle_collector.py",
      interpreter: "python3",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
    {
      name: "crypto-live",
      script: "services/trader.py",
      interpreter: "python3",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        PYTHONUNBUFFERED: "1",
        MODE: "live",
        STRATEGY_VERSION: "v2.5",
      },
    },
    {
      name: "exchange-sync",
      script: "services/exchange_sync.py",
      interpreter: "python3",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};

// ============================================================
// DEV ENVIRONMENT — isolated from production
// Branch: dev | DB: trading_monitor_dev | Ports: 3003+
// Paul works here. Never touch production directly.
// ============================================================

// NOTE: Add these manually to pm2 when needed:
// pm2 start services/trader.py --name crypto-dev --interpreter python3 -- --env DB_NAME=trading_monitor_dev MODE=paper STRATEGY_VERSION=v2.5
// pm2 start services/candle_collector.py --name candle-dev --interpreter python3 -- --env DB_NAME=trading_monitor_dev
// pm2 start services/exchange_sync.py --name exchange-sync-dev --interpreter python3 -- --env DB_NAME=trading_monitor_dev
// Dev dashboard runs on port 3003
