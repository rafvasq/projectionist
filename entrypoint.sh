#!/bin/sh
set -e

# Parse cron schedule from config.yaml and write a crontab for supercronic
CRON=$(python -c "
import yaml
cfg = yaml.safe_load(open('/app/config.yaml'))
print(cfg.get('schedule', {}).get('cron', '0 3 * * 1'))
")

echo "$CRON cd /app && python curator.py" > /app/crontab
echo "0 16 * * 5 cd /app && python friday_digest.py" >> /app/crontab
echo "0 4 * * 6 cd /app && python cleaner.py --run-deletion" >> /app/crontab
echo "0 12 * * 0 cd /app && python cleaner.py --send-notification" >> /app/crontab

echo "Projectionist starting — schedule: $CRON"

# Run once immediately on container start, then hand off to supercronic
python curator.py
python cleaner.py --run-deletion

exec supercronic /app/crontab
