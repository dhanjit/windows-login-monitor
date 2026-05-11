# windows-login-monitor

Get a push notification on your phone whenever someone logs into your Windows PC — with phone-presence detection so you don't spam yourself when *you're* the one using it.

## How it works

- **Trigger:** A Windows scheduled task fires on every user logon (interactive + unlock).
- **"Is it me?" check:** A PowerShell script pings your phone on the local Wi-Fi. If your phone answers, the script stays silent.
- **Alert:** If the phone doesn't answer, the script POSTs to [ntfy.sh](https://ntfy.sh), which pushes a notification to the **ntfy** app on your phone.

```
Logon event ──▶ Scheduled task ──▶ LoginAlert.ps1
                                       │
                                       ├─ Ping phone IP
                                       │     ├─ responds → silent
                                       │     └─ no reply → POST to ntfy.sh ──▶ phone alert
                                       └─ Append to C:\Scripts\login.log
```

## Requirements

- Windows 10 / 11
- PowerShell 5.1+ (built-in)
- Administrator access (one-time, for setup)
- A smartphone on the same Wi-Fi network as the PC
- [ntfy app](https://ntfy.sh/) installed on the phone (free, no account)

## Quick start

### 1. On your phone

- Install the **ntfy** app (Play Store / App Store)
- Find your phone's local IP:
  - **Android:** Settings → About → Status → IP address
  - **iPhone:** Settings → Wi-Fi → tap (i) next to your network
- In your router, set a **DHCP reservation** so that IP never changes

### 2. On your PC

- Download [`Setup-LoginMonitor.ps1`](./Setup-LoginMonitor.ps1)
- Right-click PowerShell → **Run as Administrator**
- Run:
  ```powershell
  Set-ExecutionPolicy -Scope Process Bypass -Force
  .\Setup-LoginMonitor.ps1
  ```
- Enter your phone's IP and (optionally) a topic name when prompted
- The script will:
  - Create `C:\Scripts\LoginAlert.ps1` with your config baked in
  - Register a scheduled task named `LoginAlert`
  - Send a test notification to confirm ntfy works

### 3. Subscribe in the ntfy app

- Open ntfy → **+** → enter the topic name shown at the end of setup
- You should see the **"Setup Test"** notification within a few seconds

## Verify

| Scenario | Expected |
|---|---|
| Phone on home Wi-Fi → lock + unlock PC | **No alert** (script sees you're home) |
| Phone Wi-Fi off / phone away → lock + unlock PC | **Alert hits ntfy app** |

Logs are written to `C:\Scripts\login.log` for every run, regardless of whether a notification was sent.

## Configuration

All config lives at the top of `C:\Scripts\LoginAlert.ps1`:

```powershell
$PhoneIP   = "192.168.1.50"          # your phone's reserved local IP
$NtfyTopic = "pc-alert-xxxxxxxx"     # your secret ntfy topic
$LogFile   = "C:\Scripts\login.log"
```

Edit and save — the scheduled task picks up the new values on the next logon.

## Notes & caveats

- **Topic = password.** Anyone who knows your ntfy topic can see your alerts. Use a long, random one.
- **Phone Wi-Fi sleeps.** Some Android/iOS power-saving modes drop Wi-Fi when the screen is off, which can cause false alarms. If you see them, tweak the script to ping 3+ times with delays, or whitelist a known-good time window.
- **RDP / network logons.** The default `AtLogOn` trigger catches console logons and unlocks. To also catch RDP, add a second trigger on Security Event ID 4624 with logon type 10.
- **Random MAC addresses** don't matter here — we identify the phone by IP via DHCP reservation, not MAC.

## Upgrade ideas

- **More reliable push:** swap ntfy.sh for [Pushover](https://pushover.net/) ($5 one-time) — same `Invoke-RestMethod` pattern, different URL.
- **Stronger presence detection:** install Tasker (Android) or Shortcuts (iOS) → on home Wi-Fi connect, hit a tiny webhook on your PC that touches a `home.flag` file. The script then checks `home.flag` mtime instead of pinging.
- **Failed-login alerts:** add a second task triggered on Event ID 4625.

## Uninstall

```powershell
Unregister-ScheduledTask -TaskName LoginAlert -Confirm:$false
Remove-Item C:\Scripts\LoginAlert.ps1
```

## License

MIT — see [LICENSE](./LICENSE).
