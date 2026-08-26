# Production scheduling

Production uses one scheduler per responsibility.

| Job | Scheduler | Schedule |
|---|---|---|
| Strategy alert scan | systemd | Every 30 minutes, isolated management-command process |
| Danta recommendations | systemd | 09:00, 09:10 KST |
| Swing recommendations | systemd | 09:20, 09:35, 09:50 KST |
| SQLite backup | systemd | 03:30 KST |
| Health monitor | systemd | Every 5 minutes, Telegram on status transitions |
| Visit-log pruning | user crontab | Daily at 04:00 |

The legacy HTTP route `/cron/scan/` remains authenticated for compatibility but no longer performs scans. `/cron/daily-picks/` and `/cron/swing-picks/` are deliberately not exposed. Heavy work therefore never runs inside Gunicorn.

## Install or update the systemd timers

```bash
cd ~/myapp
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-daily-picks.service \
  /etc/systemd/system/coinscreener-daily-picks.service
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-daily-picks.timer \
  /etc/systemd/system/coinscreener-daily-picks.timer
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-swing-picks.service \
  /etc/systemd/system/coinscreener-swing-picks.service
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-swing-picks.timer \
  /etc/systemd/system/coinscreener-swing-picks.timer
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-backup.service \
  /etc/systemd/system/coinscreener-backup.service
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-backup.timer \
  /etc/systemd/system/coinscreener-backup.timer
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-scheduled-scans.service \
  /etc/systemd/system/coinscreener-scheduled-scans.service
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-scheduled-scans.timer \
  /etc/systemd/system/coinscreener-scheduled-scans.timer
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-health.service \
  /etc/systemd/system/coinscreener-health.service
sudo install -o root -g root -m 0644 \
  deploy/systemd/coinscreener-health.timer \
  /etc/systemd/system/coinscreener-health.timer
sudo install -o root -g root -m 0644 \
  deploy/coinscreener.service \
  /etc/systemd/system/coinscreener.service
sudo install -d -o root -g root -m 0755 \
  /etc/systemd/system/upbit-crawler.service.d
sudo install -o root -g root -m 0644 \
  deploy/systemd/upbit-crawler-oom.conf \
  /etc/systemd/system/upbit-crawler.service.d/oom-priority.conf

sudo systemctl daemon-reload
sudo systemctl enable --now \
  coinscreener-daily-picks.timer \
  coinscreener-swing-picks.timer \
  coinscreener-backup.timer \
  coinscreener-scheduled-scans.timer \
  coinscreener-health.timer
systemctl list-timers \
  coinscreener-daily-picks.timer \
  coinscreener-swing-picks.timer \
  coinscreener-backup.timer \
  coinscreener-scheduled-scans.timer \
  coinscreener-health.timer \
  --all --no-pager
```

After the scheduled-scan timer is active, persist the KOSPI alert slot at 10:00 KST (03:00 Belgium summer time) and restart the services:

```bash
./venv/bin/python manage.py shell -c "from coinscreener.screener.models import AlertSetting; print(AlertSetting.objects.filter(exchange='kospi').update(alert_hour=10, alert_min=0))"
sudo systemctl restart coinscreener upbit-crawler
```

## Remove legacy duplicate schedules

In cron-job.org:

1. Disable or delete the jobs for `/cron/daily-picks/`.
2. Disable or delete the jobs for `/cron/swing-picks/`.
3. Disable or delete `/cron/scan/` after `coinscreener-scheduled-scans.timer` is active.

In the server user crontab, remove:

- the every-minute `git pull origin main` entry;
- the `generate_daily_picks` entry.

Keep the visit-log pruning entry. Back up the current crontab before editing:

```bash
crontab -l > ~/crontab.before-systemd-migration.txt
crontab -e
```

Automatic `git pull` is intentionally removed. Production deployment must be an explicit pull, test, migration, and service restart so that an unverified commit cannot become live automatically.

## Verification

```bash
systemctl list-timers \
  coinscreener-daily-picks.timer \
  coinscreener-swing-picks.timer \
  coinscreener-backup.timer \
  coinscreener-scheduled-scans.timer \
  coinscreener-health.timer \
  --all --no-pager

sudo journalctl -u coinscreener-daily-picks.service -n 50 --no-pager
sudo journalctl -u coinscreener-swing-picks.service -n 50 --no-pager
sudo journalctl -u coinscreener-backup.service -n 50 --no-pager
sudo journalctl -u coinscreener-scheduled-scans.service -n 100 --no-pager
sudo journalctl -u coinscreener-health.service -n 50 --no-pager
curl -fsS http://127.0.0.1:8000/healthz/
```

