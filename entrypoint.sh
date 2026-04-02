#!/bin/sh
set -e

# Parse cron schedule from config.yaml and write a crontab for supercronic
CRON=$(python -c "
import yaml
cfg = yaml.safe_load(open('/app/config.yaml'))
print(cfg.get('schedule', {}).get('cron', '0 3 * * 1'))
")

echo "$CRON cd /app && python curator.py" > /app/crontab

echo "Projectionist starting — schedule: $CRON"

# Run once immediately on container start, then hand off to supercronic
python curator.py

exec supercronic /app/crontab
