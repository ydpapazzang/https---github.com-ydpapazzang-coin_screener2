# Production scheduling

Production uses one scheduler per responsibility.

| Job | Scheduler | Schedule |
|---|---|---|
| Strategy alert scan | cron-job.org | Every 30 minutes, `/cron/scan/` with Bearer authentication |
| Danta recommendations | systemd | 09:00, 09:10, 09:20 KST |
| Swing recommendations | systemd | 09:20, 09:35, 09:50 KST |
| SQLite backup | systemd | 03:30 KST |
| Visit-log pruning | user crontab | Daily at 04:00 |

The HTTP routes `/cron/daily-picks/` and `/cron/swing-picks/` are deliberately not exposed. This prevents cron-job.org, a manual HTTP request, and systemd from starting the same generator concurrently.

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

sudo systemctl daemon-reload
sudo systemctl enable --now \
  coinscreener-daily-picks.timer \
  coinscreener-swing-picks.timer \
  coinscreener-backup.timer
systemctl list-timers \
  coinscreener-daily-picks.timer \
  coinscreener-swing-picks.timer \
  coinscreener-backup.timer \
  --all --no-pager
```

## Remove legacy duplicate schedules

In cron-job.org:

1. Disable or delete the jobs for `/cron/daily-picks/`.
2. Disable or delete the jobs for `/cron/swing-picks/`.
3. Keep only `/cron/scan/`, scheduled every 30 minutes with the existing `Authorization: Bearer ...` header.

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
  --all --no-pager

sudo journalctl -u coinscreener-daily-picks.service -n 50 --no-pager
sudo journalctl -u coinscreener-swing-picks.service -n 50 --no-pager
sudo journalctl -u coinscreener-backup.service -n 50 --no-pager
```
