# steam-guides TODO

## 1. Auto-detect Steam ID from API key

Currently `STEAM_ID` is hardcoded in `picker/steam_dump.py`. When a user provides their API key, resolve their Steam ID automatically so the project works out of the box without editing code.

**Approach:** Steam Web API has a `ResolveVanityURL` endpoint and `GetPlayerSummaries` can confirm ownership. The simplest path: when `--api-key` is provided but no Steam ID is known, call:
```
https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key=KEY&steamids=...
```
Or prompt the user to enter their Steam profile URL (e.g. `https://steamcommunity.com/id/username`) and resolve it via `ResolveVanityURL`. Store the result in `.steam_config` alongside the API key so it only needs to be entered once.

**Files to change:** `picker/steam_dump.py` (remove hardcoded `STEAM_ID`), `picker/app.py` (add SteamID field to settings panel)

---

## 2. Replace Obsidian with an open-source alternative

Obsidian is free but closed-source and not self-hostable. Research candidates:

| App | Notes |
|-----|-------|
| **Logseq** | Open source, block-based, Markdown files on disk, git sync built-in, active dev — closest drop-in feel |
| **Silverbullet** | Open source, self-hosted web app, Markdown, very lightweight, runs as a single binary/Docker container |
| **Foam** | VSCode extension, pure Markdown, no separate app — good if already using VSCode |
| **Joplin** | Open source, Markdown, has its own sync server (Joplin Server), mobile apps |
| **Zettlr** | Open source desktop app, Markdown, academic/Zettelkasten focus |

**Recommendation to evaluate first:** Silverbullet — it's a self-hosted web app (like the picker), already accessible from any device on the network without installing anything, and runs fine on hp-elite800 alongside Flask. No Electron, no mobile app needed.

`obsidian_convert.py` already writes clean Markdown with YAML frontmatter — output is largely compatible with all of the above.

**Action:** Spin up Silverbullet on hp-elite800, point it at `~/Documents/Steam Guides/`, test that dataview-style queries work (Silverbullet has its own query language called "Space Lua").

---

## 3. Replace Syncthing with a lightweight Python sync script

Syncthing works well but is a persistent daemon with a web UI, discovery server traffic, and ~60MB binary. For one-directional sync (hp-elite800 → other devices) a simpler approach may be enough.

**Options:**

- **rsync + SSH + cron** — simplest, already available on every Linux machine. One cron line pushes the vault after each overnight scrape. No daemon, no extra install. Downside: one-directional.
- **Python watchdog + rsync** — `watchdog` library watches the output folder; on any change it calls `rsync -avz --delete` to push to target hosts. Bidirectional requires running on both ends.
- **Unison** — bidirectional file sync over SSH, lightweight, available in most package managers. More reliable than hand-rolled rsync for two-way sync.
- **rclone** — supports 40+ backends (SFTP, S3, Google Drive, etc.), good if devices aren't always on the same LAN.

**Recommendation:** Start with rsync + SSH added to the end of the overnight cron. If bidirectional sync is needed (editing notes on another device and pushing back), use Unison.

**Example cron addition:**
```bash
rsync -avz --delete ~/Documents/Steam\ Guides/ user@othermachine:~/Documents/Steam\ Guides/
```

---

## 4. Single-command install package

Package the entire project so a new machine can be set up with one command. The standard pattern is a `curl | bash` installer script:

```bash
curl -fsSL https://raw.githubusercontent.com/smileyninja/steam-guides/main/install.sh | bash
```

**The install script should:**
1. Clone the repo to `~/steam-guides/`
2. Create the Python venv and install dependencies (`requirements.txt`)
3. Ask for Steam API key and Steam ID (or detect ID automatically — see item 1)
4. Initialize the SQLite DB and run first library sync
5. Install and enable the systemd user service for the Flask picker
6. Optionally add the cron jobs
7. Print a summary: picker URL, first scrape scheduled for tonight

**Other packaging options:**
- `pip install` via PyPI — cleaner for Python users but more setup overhead and not great for apps with system services
- Docker Compose — bundles Flask + venv, portable, but heavier; good if targeting non-Linux or server deployments
- Makefile with `make install` — middle ground, no curl required, user clones first then runs make

**Recommended path:** `install.sh` (curl-able) + `Makefile` for post-clone use. The curl script is the "stranger downloads this" path; Makefile is for development/updates.

**Files to create:** `install.sh`, `Makefile`, `requirements.txt` (top-level), optionally `steam-guides.service` systemd unit template

---

## Rough Priority Order

- [ ] Auto Steam ID detection (unblocks new installs without code edits)
- [ ] Write `install.sh` + `requirements.txt` (makes the project actually shareable)
- [ ] Evaluate Silverbullet as Obsidian replacement
- [ ] Replace Syncthing with rsync cron (or Unison if bidirectional needed)
- [ ] Full packaging (Makefile, systemd unit template, Docker Compose optional)

---

## 5. ~~Case-sensitive search~~ — Fixed

`LOWER(name) LIKE LOWER(?)` applied in `picker/app.py`. Searching "baldur" now matches "Baldur's Gate 3".

---

## 6. ~~Mature-rated games not downloading~~ — Fixed

Steam requires a `wants_mature_content_apps` cookie to serve guide pages for M-rated games (e.g. Baldur's Gate 3, Cyberpunk 2077). Without it, the scraper received a valid 200 response but 0 guide links.

**Fix applied:** Patched `steam-guide-scraper/steam_guide_scraper.py` locally to pass the mature content cookie when fetching guide listing pages. Verified BG3 now downloads 3/3 guides.

**Note:** The scraper is a third-party repo (github.com/glimgeist/steam-guide-scraper). Patch is on the local copy only — if the scraper is ever re-cloned or updated, re-apply the patch. Consider forking it under smileyninja.
