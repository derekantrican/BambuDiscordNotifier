# BambuDiscordNotifier

A lightweight, self-hosted service that monitors your **Bambu Lab** 3D printer over your local network and sends **Discord webhook notifications** for print events — with optional **Pi Camera snapshots**.

Runs on a **Raspberry Pi Zero** (or any Linux machine on the same network as your printer). No cloud services, no accounts, no subscriptions.

## Features

- 🚀 **Print Started** — notifies when a print begins
- 📊 **Progress Updates** — periodic progress with layer info, temps, and ETA
- ✅ **Print Complete** — notifies when a print finishes successfully
- ❌ **Print Failed/Cancelled** — alerts on failures
- ⏸️ **Paused / ▶️ Resumed** — tracks pause/resume events
- 🔴 **Printer Errors** — filament runout, general errors
- 📷 **Pi Camera Snapshots** — attach photos to Discord notifications
- 🎨 **Rich Discord Embeds** — color-coded with progress bars and printer stats

## Prerequisites

- A **Bambu Lab** 3D printer on your local network (P1P, P1S, X1C, A1, etc.)
- A **Raspberry Pi Zero** (or any Linux machine) connected to the same network
- **Python 3.7+**
- A **Discord webhook URL** ([how to create one](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks))
- (Optional) A **Pi Camera** connected to the Raspberry Pi

## Quick Setup

### 1. Get Your Printer Info

You'll need three things from your Bambu Lab printer:

| Setting | Where to Find It |
|---------|-----------------|
| **IP Address** | Printer LCD → Settings → Network |
| **Access Code** | Printer LCD → Settings → Network → LAN Access Code |
| **Serial Number** | Printer LCD → Settings → General, or on the printer's label |

### 2. Create a Discord Webhook

1. Open Discord → go to your server
2. Right-click a channel → **Edit Channel** → **Integrations** → **Webhooks**
3. Click **New Webhook** → copy the **Webhook URL**

### 3. Install on Your Pi

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/BambuDiscordNotifier.git
cd BambuDiscordNotifier

# Run the setup script — it will prompt for your config
bash setup.sh
```

The setup script will:
- Install system dependencies (Python, libcamera)
- Create a Python virtual environment
- Install pip packages
- Prompt you for printer IP, access code, serial number, and webhook URL
- Generate `config.yaml`
- Install and start a systemd service

### Manual Setup (Alternative)

```bash
# Install dependencies
sudo apt-get install python3 python3-venv libcamera-apps-lite

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create your config
cp config.example.yaml config.yaml
nano config.yaml  # Fill in your settings

# Run it
python run.py
```

## Configuration

Edit `config.yaml`:

```yaml
printer:
  name: "My Bambu Lab P1S"
  ip: "192.168.1.100"
  access_code: "12345678"
  serial_number: "01P00A000000"
  port: 8883

discord:
  webhook_url: "https://discord.com/api/webhooks/..."
  mention_role_id: null              # @mention a role on failures
  events:
    started: true
    progress: true
    progress_interval: 25            # notify every 25%
    done: true
    failed: true
    paused: true
    resumed: true
    error: true

camera:
  enabled: true
  method: "libcamera"                # or "picamera2"
  resolution: [1280, 720]
  include_on_events: [done, failed, progress]

logging:
  level: "INFO"
  file: null
```

### Configuration Details

| Setting | Description |
|---------|-------------|
| `printer.ip` | Your printer's local IP address |
| `printer.access_code` | The LAN access code from your printer's settings |
| `printer.serial_number` | Your printer's serial number |
| `discord.webhook_url` | Discord webhook URL for notifications |
| `discord.mention_role_id` | Optional Discord role ID to @mention on errors/failures |
| `discord.events.progress_interval` | Send progress every N% (default 25 = at 25%, 50%, 75%) |
| `camera.method` | `"libcamera"` uses subprocess, `"picamera2"` uses Python library |
| `camera.include_on_events` | Which events include a camera snapshot |

## Service Management

```bash
# Check status
sudo systemctl status bambu-discord-notifier

# View live logs
sudo journalctl -u bambu-discord-notifier -f

# Restart after config changes
sudo systemctl restart bambu-discord-notifier

# Stop the service
sudo systemctl stop bambu-discord-notifier
```

## How It Works

```
Bambu Printer ──MQTT/TLS──► BambuClient ──► StateTranslator ──► DiscordNotifier ──► Discord
  (port 8883)                                   │                     │
                                                │                     │
                                            Pi Camera ──snapshot──────┘
```

1. **BambuClient** connects to your printer via MQTT over TLS (port 8883) on your local network
2. The printer sends JSON state updates (temperatures, progress, gcode state)
3. **StateTranslator** watches for state transitions (IDLE → RUNNING → FINISH, etc.)
4. When events occur, **DiscordNotifier** formats rich embeds and POSTs them to your webhook
5. If enabled, **PiCamCapture** takes a snapshot and attaches it to the Discord message

## Troubleshooting

### "Connection failed" / can't connect to printer
- Verify the printer IP, access code, and serial number in `config.yaml`
- Make sure the Pi and printer are on the same network
- Check that LAN mode is enabled on your printer (Settings → Network)

### "Disconnected while subscribing"
- Usually means the **access code** or **serial number** is wrong
- Double-check both values match what's on your printer's LCD

### No camera snapshots
- Run `libcamera-still -o test.jpg` to test the camera directly
- Make sure the camera cable is properly seated
- Check `camera.enabled: true` and the event is in `camera.include_on_events`

### Discord not receiving messages
- Test your webhook URL: `curl -X POST -H "Content-Type: application/json" -d '{"content":"test"}' YOUR_WEBHOOK_URL`
- Check for rate limiting in logs

## Credits

MQTT protocol and Bambu state handling inspired by [OctoEverywhere](https://github.com/QuinnDameworthy/OctoEverywhere) (MIT License).

## License

MIT
