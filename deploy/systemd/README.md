# Swing recommendation systemd timer

The production GCP VM should run `generate_swing_picks` directly with systemd
instead of relying on an HTTP request that returns before generation finishes.

The timer runs at 09:20, 09:35, and 09:50 Asia/Seoul. The first successful run
creates the day's Swing result; later attempts exit safely because the command
detects that a result already exists. If an external API or database failure
causes the first attempt to exit non-zero, a later timer invocation retries it.

## Install

From `/home/i_am_psw86/myapp`:

```bash
sudo cp deploy/systemd/coinscreener-swing-picks.service /etc/systemd/system/
sudo cp deploy/systemd/coinscreener-swing-picks.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coinscreener-swing-picks.timer
```

After enabling the timer, disable the external cron-job.org Swing job to avoid
two independent schedulers.

## Verify

```bash
systemctl list-timers coinscreener-swing-picks.timer --all --no-pager
sudo systemctl status coinscreener-swing-picks.timer --no-pager -l
sudo journalctl -u coinscreener-swing-picks.service -n 100 --no-pager
```

Do not manually start the service outside the command's KST 09:00-10:59
generation window unless a deliberately forced test is required.
