#!/usr/bin/env python3
"""
steam_dump.py — Steam library enricher + guide picker
Fetches owned games, enriches with SteamSpy tags, filters to
guide-worthy genres, writes a TSV for manual selection.

Usage:
    python3 steam_dump.py --api-key YOUR_KEY
    python3 steam_dump.py --api-key YOUR_KEY --all        # skip genre filter
    python3 steam_dump.py --from-file                     # skip fetch, go to scraper
    python3 steam_dump.py --refresh-tags                  # re-fetch SteamSpy data

Output:
    ~/steam-guides/game-picker.tsv   — edit SELECTED column, then run --from-file
    ~/steam-guides/.enrichment-cache.json  — SteamSpy cache (persistent)
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────
STEAM_ID     = "76561198013213933"
GUIDES_DIR   = Path.home() / "steam-guides"
SCRAPER_DIR  = GUIDES_DIR / "steam-guide-scraper"
OUTPUT_DIR   = GUIDES_DIR
PICKER_FILE  = GUIDES_DIR / "game-picker.tsv"
CACHE_FILE   = GUIDES_DIR / ".enrichment-cache.json"
DB_FILE      = GUIDES_DIR / "steam_picker.db"

# Genre tags to keep — edit this list to taste
GUIDE_WORTHY_TAGS = {
    # Strategy
    "strategy", "turn-based strategy", "4x", "grand strategy",
    "real-time strategy", "rts", "turn-based tactics", "wargame",
    "hex grid", "historical", "political sim",
    # Factory / Builder
    "base building", "factory", "city builder", "colony sim",
    "automation", "resource management", "building", "management",
    "economy", "logistics",
    # Survival / Crafting
    "survival", "crafting", "open world survival craft",
    "survival horror", "base-building", "exploration",
    # RPG
    "rpg", "action rpg", "crpg", "tactical rpg", "dungeon crawler",
    "jrpg", "turn-based rpg", "isometric", "party-based rpg",
    "character customization", "loot", "hack and slash",
    # Space / Sci-fi (often overlap with 4X/strategy)
    "space", "space sim", "sci-fi",
    # Sandbox
    "sandbox", "open world", "procedural generation",
}

# Tags that disqualify a game even if it matches above
EXCLUDE_TAGS = {
    "visual novel", "dating sim", "sports", "racing", "football",
    "soccer", "golf", "tennis", "basketball", "baseball",
    "shoot 'em up", "shmup", "bullet hell", "rhythm", "music",
    "educational", "typing",
}

# ── Cache ──────────────────────────────────────────────────────────────────
def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))

# ── Steam API ──────────────────────────────────────────────────────────────
def fetch_owned_games(api_key: str) -> list[dict]:
    """Returns list of {appid, name, playtime_forever}"""
    url = (
        "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
        f"?key={api_key}&steamid={STEAM_ID}"
        "&include_appinfo=true&include_played_free_games=true&format=json"
    )
    print("Fetching owned games from Steam API...")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    games = r.json().get("response", {}).get("games", [])
    print(f"  Got {len(games)} games ({sum(1 for g in games if g.get('playtime_forever', 0) > 0)} played)")
    return games

# ── SteamSpy ───────────────────────────────────────────────────────────────
def fetch_steamspy(appid: int, session: requests.Session) -> dict:
    """Fetch tags, genres, scores from SteamSpy."""
    url = f"https://steamspy.com/api.php?request=appdetails&appid={appid}"
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}

def enrich_games(games: list[dict], cache: dict, refresh: bool = False) -> list[dict]:
    """Add SteamSpy data to each game. Uses cache to avoid re-fetching."""
    uncached = [g for g in games if str(g["appid"]) not in cache or refresh]

    if uncached:
        print(f"Fetching SteamSpy data for {len(uncached)} games")
        print(f"  Estimated time: {len(uncached) // 60 + 1} min at 1 req/sec")
        print(f"  (cached forever — only runs once per game)")
        print()

        session = requests.Session()
        for i, game in enumerate(uncached):
            appid = game["appid"]
            data = fetch_steamspy(appid, session)
            cache[str(appid)] = data

            name = game.get("name") or data.get("name") or f"[{appid}]"
            tags_raw = data.get("tags", {})
            if isinstance(tags_raw, dict):
                top_tags = list(sorted(tags_raw, key=lambda t: -tags_raw[t]))[:3]
            elif isinstance(tags_raw, list):
                top_tags = tags_raw[:3]
            else:
                top_tags = []
            tag_str = ", ".join(top_tags) if top_tags else "no tags"
            pct = (i + 1) / len(uncached) * 100
            print(f"  [{i+1:4d}/{len(uncached)}] {pct:5.1f}%  {name[:40]:<40}  {tag_str}")

            if (i + 1) % 100 == 0:
                save_cache(cache)
            time.sleep(1.05)  # SteamSpy rate limit

        save_cache(cache)
        print(f"  Done. Cached to {CACHE_FILE.name}")
    else:
        print(f"All {len(games)} games loaded from cache.")

    # Merge cache data back into games
    enriched = []
    for game in games:
        spy = cache.get(str(game["appid"]), {})
        tags_raw = spy.get("tags", {})
        # Tags come back as {tag_name: vote_count} dict
        if isinstance(tags_raw, dict):
            tags = sorted(tags_raw.keys(), key=lambda t: -tags_raw[t])  # sort by votes
        elif isinstance(tags_raw, list):
            tags = tags_raw
        else:
            tags = []

        genres_raw = spy.get("genre", "")
        genres = [g.strip() for g in genres_raw.split(",")] if genres_raw else []

        enriched.append({
            "appid":     game["appid"],
            "name":      game.get("name", spy.get("name", f"[{game['appid']}]")),
            "playtime":  game.get("playtime_forever", 0),  # minutes
            "tags":      tags[:8],   # top 8 by vote count
            "genres":    genres,
            "positive":  spy.get("positive", 0),
            "negative":  spy.get("negative", 0),
            "score":     spy.get("score_rank", ""),
        })

    return enriched

# ── Genre filter ───────────────────────────────────────────────────────────
def is_guide_worthy(game: dict) -> bool:
    all_tags = {t.lower() for t in game["tags"] + game["genres"]}
    if all_tags & EXCLUDE_TAGS:
        return False
    return bool(all_tags & GUIDE_WORTHY_TAGS)

# ── Existing downloads ─────────────────────────────────────────────────────
def find_existing() -> dict[int, tuple[Path, int]]:
    """Returns {appid: (folder_path, guide_count)}"""
    existing = {}
    skip = {"steam-guide-scraper", "picker"}
    for d in OUTPUT_DIR.iterdir():
        if not d.is_dir() or d.name.startswith(".") or d.name in skip:
            continue
        if d.name.isdigit():
            appid = int(d.name)
        else:
            # Named folder — scan first .md for game_id
            mds = list(d.glob("*.md"))
            appid = None
            for md in mds[:3]:
                try:
                    for line in md.read_text(errors="replace").splitlines():
                        if line.startswith("game_id:"):
                            appid = int(line.split(":")[1].strip())
                            break
                except Exception:
                    pass
                if appid:
                    break
            if not appid:
                continue
        count = len(list(d.glob("*.md")))
        existing[appid] = (d, count)
    return existing

# ── SQLite DB ─────────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS games (
            appid       INTEGER PRIMARY KEY,
            name        TEXT,
            playtime    INTEGER,
            tags        TEXT,
            genres      TEXT,
            positive    INTEGER,
            negative    INTEGER,
            score       TEXT,
            downloaded  INTEGER DEFAULT 0,
            guide_count INTEGER DEFAULT 0,
            selected    INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def sync_to_db(enriched: list[dict], existing: dict):
    conn = init_db()
    for g in enriched:
        appid = g["appid"]
        downloaded  = 1 if appid in existing else 0
        guide_count = existing[appid][1] if appid in existing else 0
        conn.execute("""
            INSERT INTO games
                (appid, name, playtime, tags, genres, positive, negative, score, downloaded, guide_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=excluded.name, playtime=excluded.playtime,
                tags=excluded.tags, genres=excluded.genres,
                positive=excluded.positive, negative=excluded.negative,
                score=excluded.score, downloaded=excluded.downloaded,
                guide_count=excluded.guide_count
        """, (
            appid, g["name"], g["playtime"],
            json.dumps(g["tags"]), json.dumps(g["genres"]),
            g["positive"], g["negative"], str(g["score"]),
            downloaded, guide_count,
        ))
    conn.commit()
    conn.close()
    print(f"  Synced {len(enriched)} games to {DB_FILE.name}")


# ── Write TSV ──────────────────────────────────────────────────────────────
def write_picker(games: list[dict], existing: dict, filter_genres: bool):
    if filter_genres:
        worthy = [g for g in games if is_guide_worthy(g)]
        skipped = len(games) - len(worthy)
    else:
        worthy = games
        skipped = 0

    # Sort: already-downloaded first, then by playtime desc
    def sort_key(g):
        downloaded = 0 if g["appid"] in existing else 1
        return (downloaded, -g["playtime"])

    worthy.sort(key=sort_key)

    lines = [
        "# Steam Guide Picker",
        "# Edit the SELECTED column: put an x to queue a game for guide download",
        "# Then run: python3 steam_dump.py --from-file",
        "#",
        "# Columns: SELECTED | APPID | PLAYTIME | DOWNLOADED | GUIDES | TAGS | NAME",
        "#",
    ]

    if filter_genres and skipped:
        lines.append(f"# Filtered to guide-worthy genres. {skipped} games hidden (casual/sports/etc).")
        lines.append("# Run with --all to see every game.")
        lines.append("#")

    lines.append("\t".join(["SELECTED", "APPID", "PLAYTIME", "DOWNLOADED", "GUIDES", "TAGS", "NAME"]))

    for g in worthy:
        appid = g["appid"]
        hrs = g["playtime"] // 60
        mins = g["playtime"] % 60
        playtime_str = f"{hrs}h{mins:02d}m" if hrs > 0 else f"{mins}m"

        if appid in existing:
            folder, count = existing[appid]
            downloaded = "yes"
            guides_str = str(count)
            selected = "x"  # pre-select already-downloaded
        else:
            downloaded = "no"
            guides_str = ""
            selected = " "

        tags_str = ", ".join(g["tags"][:5])
        name = g["name"].replace("\t", " ")

        lines.append("\t".join([selected, str(appid), playtime_str, downloaded, guides_str, tags_str, name]))

    PICKER_FILE.write_text("\n".join(lines))

    played = sum(1 for g in worthy if g["playtime"] > 0)
    downloaded = sum(1 for g in worthy if g["appid"] in existing)

    print(f"\n{'─'*60}")
    print(f"  Written: {PICKER_FILE}")
    print(f"  Games in list:     {len(worthy)}")
    print(f"  Played (>0 min):   {played}")
    print(f"  Already downloaded:{downloaded} (pre-selected)")
    if skipped:
        print(f"  Hidden (filtered): {skipped}")
    print(f"{'─'*60}")
    print()
    print("  Next steps:")
    print(f"  1. Open {PICKER_FILE.name} in any text editor")
    print("  2. Put 'x' in the SELECTED column for games you want")
    print("  3. Run: python3 steam_dump.py --from-file")
    print()

# ── Read TSV and run scraper ───────────────────────────────────────────────
def safe_dirname(name: str) -> str:
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()

def sync_downloaded_from_fs():
    existing = find_existing()
    conn = init_db()
    conn.execute("UPDATE games SET downloaded=0, guide_count=0")
    for appid, (folder, count) in existing.items():
        conn.execute(
            "UPDATE games SET downloaded=1, guide_count=? WHERE appid=?",
            (count, appid)
        )
    conn.commit()
    updated = conn.execute("SELECT COUNT(*) FROM games WHERE downloaded=1").fetchone()[0]
    conn.close()
    print(f"  Filesystem sync: {updated} games marked downloaded ({len(existing)} folders found)")


def migrate_tsv_to_db():
    if not PICKER_FILE.exists():
        print(f"TSV not found: {PICKER_FILE}")
        sys.exit(1)
    conn = init_db()
    cache = load_cache()
    count = 0
    for line in PICKER_FILE.read_text().splitlines():
        if line.startswith("#") or line.startswith("SELECTED") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        sel, appid_str, playtime_str, dl_str, guides_str, tags_str, name = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
        )
        try:
            appid = int(appid_str.strip())
        except ValueError:
            continue
        # Parse "105h23m" or "45m" back to minutes
        import re
        m = re.match(r'(?:(\d+)h)?(?:(\d+)m)?', playtime_str.strip())
        hours = int(m.group(1) or 0)
        mins  = int(m.group(2) or 0)
        playtime = hours * 60 + mins
        downloaded  = 1 if dl_str.strip() == "yes" else 0
        guide_count = int(guides_str.strip()) if guides_str.strip().isdigit() else 0
        selected_val = 1 if sel.strip().lower() in ("x", "✓", "yes", "y", "1") else 0
        # Get full tags/genres from SteamSpy cache
        spy = cache.get(str(appid), {})
        tags_raw = spy.get("tags", {})
        if isinstance(tags_raw, dict):
            tags = sorted(tags_raw, key=lambda t: -tags_raw[t])[:8]
        elif isinstance(tags_raw, list):
            tags = tags_raw[:8]
        else:
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        genres_raw = spy.get("genre", "")
        genres = [g.strip() for g in genres_raw.split(",")] if genres_raw else []
        conn.execute("""
            INSERT INTO games
                (appid, name, playtime, tags, genres, downloaded, guide_count, selected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                name=excluded.name, playtime=excluded.playtime,
                tags=excluded.tags, genres=excluded.genres,
                downloaded=excluded.downloaded, guide_count=excluded.guide_count,
                selected=excluded.selected
        """, (appid, name.strip(), playtime, json.dumps(tags), json.dumps(genres),
              downloaded, guide_count, selected_val))
        count += 1
    conn.commit()
    conn.close()
    print(f"  Migrated {count} games from TSV → {DB_FILE.name}")
    print(f"  Web UI: http://hp-elite800:5001")


def _run_scraper(selected: list[int], names: dict, limit: int, sort_by: str):
    print(f"Selected {len(selected)} games:")
    for a in selected:
        print(f"  • {names.get(a, a)} ({a})")
    print()
    confirm = input(f"Download top {limit} {sort_by} guides per game? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    venv_python = SCRAPER_DIR / "venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    scraper = SCRAPER_DIR / "steam_guide_scraper.py"
    if not scraper.exists():
        print(f"Scraper not found: {scraper}")
        sys.exit(1)

    cache = load_cache()
    for appid in selected:
        name = names.get(appid, str(appid))
        spy_name = cache.get(str(appid), {}).get("name", "")
        folder_name = safe_dirname(spy_name or name)
        out = OUTPUT_DIR / folder_name
        print(f"\n{'─'*60}")
        print(f"  {name} ({appid})")
        print(f"  → {out}")
        print(f"{'─'*60}")
        cmd = [
            python, str(scraper),
            "--game-id", str(appid),
            "--output", str(out),
            "--sort-by", sort_by,
            "--limit", str(limit),
            "--delay", "3.0",
            "--retries", "3",
        ]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  ⚠ Scraper returned code {result.returncode}")
    print("\n✓ Done.")


def run_from_db(limit: int, sort_by: str):
    if not DB_FILE.exists():
        print(f"Database not found: {DB_FILE}")
        print("Run steam_dump.py --api-key YOUR_KEY first to populate it.")
        sys.exit(1)
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT appid, name FROM games WHERE selected=1 ORDER BY name").fetchall()
    conn.close()
    if not rows:
        print("No games selected.")
        print("Open http://hp-elite800:5001 to select games in the web UI.")
        return
    selected = [r[0] for r in rows]
    names    = {r[0]: r[1] for r in rows}
    _run_scraper(selected, names, limit, sort_by)


def run_from_file(limit: int, sort_by: str):
    if DB_FILE.exists():
        run_from_db(limit, sort_by)
        return

    if not PICKER_FILE.exists():
        print(f"Neither {DB_FILE.name} nor {PICKER_FILE.name} found.")
        print("Run steam_dump.py --api-key YOUR_KEY first.")
        sys.exit(1)

    selected, names = [], {}
    for line in PICKER_FILE.read_text().splitlines():
        if line.startswith("#") or line.startswith("SELECTED"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        sel, appid_str = parts[0], parts[1]
        name = parts[6]
        if sel.strip().lower() in ("x", "✓", "yes", "y", "1"):
            try:
                appid = int(appid_str.strip())
                selected.append(appid)
                names[appid] = name.strip()
            except ValueError:
                pass
    if not selected:
        print("No games selected in picker file.")
        print(f"Edit {PICKER_FILE} and put 'x' in the SELECTED column.")
        return
    _run_scraper(selected, names, limit, sort_by)

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Steam library enricher + guide picker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  First run (generates game-picker.tsv):
    python3 steam_dump.py --api-key YOUR_KEY

  Show all games, not just guide-worthy:
    python3 steam_dump.py --api-key YOUR_KEY --all

  After editing game-picker.tsv:
    python3 steam_dump.py --from-file

  Download up to 50 top-rated guides per selected game:
    python3 steam_dump.py --from-file --limit 50

  Force refresh SteamSpy tags:
    python3 steam_dump.py --api-key YOUR_KEY --refresh-tags
        """
    )
    parser.add_argument("--api-key",      help="Steam Web API key")
    parser.add_argument("--all",          action="store_true",
                        help="Include all games, not just guide-worthy genres")
    parser.add_argument("--from-file",    action="store_true",
                        help="Skip fetch, read game-picker.tsv and run scraper")
    parser.add_argument("--refresh-tags", action="store_true",
                        help="Re-fetch SteamSpy data even if cached")
    parser.add_argument("--limit",        type=int, default=25,
                        help="Max guides per game (default: 25)")
    parser.add_argument("--sort-by",      default="toprated",
                        choices=["toprated", "trend", "mostrecent"],
                        help="Guide sort order (default: toprated)")
    parser.add_argument("--migrate-tsv",  action="store_true",
                        help="Import existing game-picker.tsv into the SQLite DB")
    parser.add_argument("--sync-fs",      action="store_true",
                        help="Rescan filesystem and update downloaded/guide_count in DB")
    args = parser.parse_args()

    GUIDES_DIR.mkdir(exist_ok=True)

    # ── Mode: sync filesystem → DB ───────────────────────────────────────
    if args.sync_fs:
        sync_downloaded_from_fs()
        return

    # ── Mode: migrate TSV → DB ────────────────────────────────────────────
    if args.migrate_tsv:
        migrate_tsv_to_db()
        return

    # ── Mode: run scraper from existing picker file ────────────────────────
    if args.from_file:
        run_from_file(args.limit, args.sort_by)
        return

    # ── Mode: fetch + enrich + write picker ───────────────────────────────
    if not args.api_key:
        # Try stored API key file
        key_file = GUIDES_DIR / ".steam_api_key"
        if key_file.exists():
            args.api_key = key_file.read_text().strip()
            print(f"Using stored API key from {key_file.name}")

    if not args.api_key:
        # Try to use steam-games.txt as fallback
        games_txt = GUIDES_DIR / "steam-games.txt"
        if games_txt.exists():
            print("No --api-key provided. Using steam-games.txt (no playtime data).")
            raw_ids = json.loads(games_txt.read_text())
            games = [{"appid": a, "playtime_forever": 0} for a in raw_ids]
        else:
            print("Provide --api-key YOUR_KEY or save key to ~/.steam-guides/.steam_api_key")
            print("Get a key at: https://steamcommunity.com/dev/apikey")
            sys.exit(1)
    else:
        games = fetch_owned_games(args.api_key)

    cache = load_cache()
    enriched = enrich_games(games, cache, refresh=args.refresh_tags)
    existing = find_existing()

    print(f"\nFound {len(existing)} already-downloaded game folders in {OUTPUT_DIR}")
    write_picker(enriched, existing, filter_genres=not args.all)
    sync_to_db(enriched, existing)
    print(f"  Web UI: http://hp-elite800:5001")

if __name__ == "__main__":
    main()
