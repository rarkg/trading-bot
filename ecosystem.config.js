module.exports = {
  apps: [{
    name: "trading-scanner",
    script: "/Library/Developer/CommandLineTools/usr/bin/python3",
    args: "/Users/elio/Projects/trading-bot/scanner.py",
    cwd: "/Users/elio/Projects/trading-bot",
    autorestart: true,
    max_restarts: 10,
    restart_delay: 5000,
    env: {
      PYTHONUNBUFFERED: "1"
    }
  }]
};
