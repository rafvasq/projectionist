#!/bin/sh
set -e

# If arguments are passed, execute them directly
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Parse cron schedule from config.yaml and write a crontab for supercronic
CRON=$(python -c "
import yaml
cfg = yaml.safe_load(open('/app/config.yaml'))
print(cfg.get('schedule', {}).get('cron', '0 3 * * 1'))
")

echo "$CRON cd /app && python -m jobs.curator" > /app/crontab
echo "0 12 * * 5 cd /app && python -m jobs.digest" >> /app/crontab
echo "0 4 * * 4 cd /app && python -m jobs.cleaner" >> /app/crontab

echo "Projectionist starting — schedule: $CRON"

# Run once immediately on container start, then hand off to supercronic
python -m jobs.curator
python -m jobs.cleaner
if [ "$(date +%u)" = "5" ] || [ "$FORCE_DIGEST" = "1" ]; then
    python -m jobs.digest
fi

exec supercronic /app/crontab
