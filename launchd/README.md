# LaunchAgent templates

Copy to `~/Library/LaunchAgents/` and load:

```bash
cp com.marylandproperty.*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.marylandproperty.refresh.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.marylandproperty.dashboard.plist
```

Gotchas (learned 2026-09-29, both silent failures):
- **stdout/stderr paths must be on the boot volume** — paths on
  `/Volumes/HulkBuster` (or any external volume) make launchd exit
  `EX_CONFIG (78)` on every fire, before the script runs.
- **`/bin/bash` needs Full Disk Access** (System Settings → Privacy &
  Security) to write to the external volume when spawned by launchd;
  without it jobs exit 126. Terminal-spawned bash is unaffected, so
  manual runs pass while launchd runs fail.
