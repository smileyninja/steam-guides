# steam-guides

Scrapes Steam community guides for games in your library, converts them to markdown, and serves them via SilverBullet (self-hosted web app) while also syncing to Obsidian via Syncthing.

## Components

| File | Purpose |
|------|---------|
| `picker/app.py` | Flask web app — browse/select games, trigger downloads |
| `picker/steam_dump.py` | Fetches Steam library, enriches with SteamSpy tags, manages SQLite DB |
| `scrape_overnight.sh` | Overnight batch scraper (runs selected games not yet downloaded) |
| `obsidian_convert.py` | Converts raw `.md` guides → Obsidian format with frontmatter and dataview index; also copies to SilverBullet space |
| `silverbullet.service` | systemd user service template for SilverBullet |

## Setup

**Requirements:** Python 3.10+, Flask, a Steam Web API key ([get one here](https://steamcommunity.com/dev/apikey))

```bash
# Install scraper dependencies
cd steam-guide-scraper
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Populate the database with your Steam library
cd picker
python3 steam_dump.py --api-key YOUR_STEAM_API_KEY

# Start the web picker
python3 app.py   # http://localhost:5001
```

## Web Picker

Browse your Steam library at `http://localhost:5001`. Filter by tag, genre, or search by name. Select games and use the overnight scrape to batch-download guides, or hit **↓ Get** on any row to download immediately.

The Steam API key can also be saved in the UI via the ⚙ settings panel.

## Overnight Pipeline

Add to crontab (`crontab -e`) to run nightly at 2 AM:

```
0 2 * * * /bin/bash ~/steam-guides/scrape_overnight.sh && python3 ~/steam-guides/obsidian_convert.py >> ~/steam-guides/overnight_scrape.log 2>&1
```

For weekly Steam library refresh (Sundays at 1 AM):

```
0 1 * * 0 /path/to/venv/bin/python ~/steam-guides/picker/steam_dump.py --api-key $(cat ~/steam-guides/.steam_api_key) >> ~/steam-guides/overnight_scrape.log 2>&1
```

Save your API key to `~/steam-guides/.steam_api_key` so the cron job can read it.

## Output

- Raw guides saved to `~/steam-guides/<Game Name>/`
- Converted guides written to `~/Sync/Steam Guides/` (Syncthing → Obsidian on all devices)
- Copied to `~/silverbullet/space/Steam Guides/` (served by SilverBullet)

## SilverBullet

SilverBullet is a self-hosted web note app that serves the guides over HTTPS on your Tailnet. Access from any device via browser — no Obsidian install needed.

**Install (binary, Linux x86_64):**

```bash
mkdir -p ~/silverbullet/space
curl -L -o /tmp/silverbullet.zip https://github.com/silverbulletmd/silverbullet/releases/latest/download/silverbullet-server-linux-x86_64.zip
unzip /tmp/silverbullet.zip -d ~/silverbullet/
chmod +x ~/silverbullet/silverbullet
```

**systemd user service:**

```bash
cp silverbullet.service ~/.config/systemd/user/silverbullet.service
# Edit SB_USER=username:password in the service file before enabling
systemctl --user daemon-reload
systemctl --user enable --now silverbullet
loginctl enable-linger $USER
```

**HTTPS via Tailscale (run once):**

```bash
sudo tailscale set --operator=$USER
tailscale serve --bg 3000
```

Access at `https://<hostname>.<tailnet>.ts.net` from any Tailscale device.

**Upgrade:**

```bash
~/silverbullet/silverbullet upgrade
systemctl --user restart silverbullet
```
