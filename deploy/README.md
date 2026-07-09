# Scheduling the daily routine (macOS launchd)

This runs `jobhunter.cli routine` every morning at 07:00 local time. launchd (not cron)
is used so a run missed while the Mac was asleep fires when it next wakes.

## Install

```bash
chmod +x "/Users/nithisha/Documents/Jobs/Fetch jobs/deploy/run_routine.sh"
cp "/Users/nithisha/Documents/Jobs/Fetch jobs/deploy/com.jobhunter.daily.plist" \
   ~/Library/LaunchAgents/com.jobhunter.daily.plist
launchctl load ~/Library/LaunchAgents/com.jobhunter.daily.plist
```

## Verify / operate

```bash
launchctl list | grep jobhunter          # is it registered?
launchctl start com.jobhunter.daily      # run once now (test)
tail -f "/Users/nithisha/Documents/Jobs/Fetch jobs/data/logs/"routine-*.log
```

## Change the time

Edit `Hour`/`Minute` in the plist, then:

```bash
launchctl unload ~/Library/LaunchAgents/com.jobhunter.daily.plist
cp deploy/com.jobhunter.daily.plist ~/Library/LaunchAgents/com.jobhunter.daily.plist
launchctl load ~/Library/LaunchAgents/com.jobhunter.daily.plist
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.jobhunter.daily.plist
rm ~/Library/LaunchAgents/com.jobhunter.daily.plist
```

## Caveat (your chosen host: your Mac)

The routine only runs when the Mac is on and awake. If it's shut down at 07:00, launchd
fires the job at next wake. If you want it to run even when the laptop is closed, that's
the case for the small VPS option in BLUEPRINT §14. The daily report lands in
`data/reports/latest.md` either way.
