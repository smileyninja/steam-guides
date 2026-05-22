#!/usr/bin/env python3
"""
Convert scraped Steam guides to Obsidian-friendly markdown.

Source:  ~/steam-guides/<Game Name>/<guide_id>.md  (read-only)
Output:  ~/Sync/Steam Guides/<Game Name>/<guide_id>.md
         ~/Documents/Obsidian Vault/Steam Guides/<Game Name>/index.md
         ~/Documents/Obsidian Vault/Steam Guides/index.md
"""

import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

import yaml

SOURCE  = Path.home() / "steam-guides"
DEST    = Path.home() / "Sync" / "Steam Guides"
SB_DEST = Path.home() / "silverbullet" / "space" / "Steam Guides"

# Folders to skip (appid-only folders handled separately, misc files)
SKIP = {
    "steam-guide-scraper", "claude chat",
    "appids.txt", "dark.css", "convert-guides.sh",
    "guide_pipeline.sh", "game-picker.tsv", "steam-games.txt",
    "scrape_overnight.sh", "obsidian_convert.py", "overnight_scrape.log",
}


# ── URL helpers ──────────────────────────────────────────────────────────────

def decode_steam_url(url: str) -> str:
    """Unwrap https://steamcommunity.com/linkfilter/?u=<encoded> → real URL."""
    if "steamcommunity.com/linkfilter" in url:
        qs = parse_qs(urlparse(url).query)
        if "u" in qs:
            return unquote(qs["u"][0])
    return url


# ── Markdown fixups ──────────────────────────────────────────────────────────

# [![Image](img_url)](link_url)  →  ![Image](img_url)
# also handles empty variants like [![Image]()](<>)
IMAGE_LINK_RE = re.compile(
    r'\[!\[([^\]]*)\]\(([^)]*)\)\]\(<([^>]*)>\)'  # angle-bracket URL variant
    r'|'
    r'\[!\[([^\]]*)\]\(([^)]*)\)\]\(([^)]*)\)'    # plain URL variant
)

def fix_image_links(text: str) -> str:
    def _replace(m: re.Match) -> str:
        if m.group(1) is not None:
            # angle-bracket variant
            alt, img = m.group(1), m.group(2)
        else:
            alt, img = m.group(4), m.group(5)
        if not img:
            return ""  # drop broken empty images
        return f"![{alt}]({img})"
    return IMAGE_LINK_RE.sub(_replace, text)


LINK_RE = re.compile(r'\[([^\]]+)\]\(<([^>]+)>\)')

def fix_angle_links(text: str) -> str:
    """[text](<url>) → [text](decoded_url)"""
    def _replace(m: re.Match) -> str:
        label, url = m.group(1), decode_steam_url(m.group(2))
        return f"[{label}]({url})"
    return LINK_RE.sub(_replace, text)


PLAIN_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://steamcommunity\.com/linkfilter/[^)]+)\)')

def fix_plain_steam_links(text: str) -> str:
    """[text](steamcommunity linkfilter url) → [text](real url)"""
    def _replace(m: re.Match) -> str:
        label, url = m.group(1), decode_steam_url(m.group(2))
        return f"[{label}]({url})"
    return PLAIN_LINK_RE.sub(_replace, text)


def fix_body(text: str) -> str:
    text = fix_image_links(text)
    text = fix_angle_links(text)
    text = fix_plain_steam_links(text)
    return text


# ── YAML helpers ─────────────────────────────────────────────────────────────

def normalize_frontmatter(fm: dict) -> dict:
    out = {}

    # keep useful fields
    for key in ("game_id", "game_title", "guide_id", "guide_title",
                "authors", "post_date", "update_date", "rating",
                "num_ratings", "unique_visitors", "current_favorites"):
        if fm.get(key) not in (None, 0, "", [], {}):
            out[key] = fm[key]

    # category → tags
    cats = fm.get("category")
    if cats:
        if isinstance(cats, str):
            cats = [cats]
        out["tags"] = [c.lower().replace(" ", "-").replace("/", "-") for c in cats]

    # steam guide URL
    if "guide_id" in fm:
        out["source"] = f"https://steamcommunity.com/sharedfiles/filedetails/?id={fm['guide_id']}"

    return out


def parse_guide(path: Path) -> tuple[dict, str] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.index("---", 3)
    raw_fm = text[3:end]
    body = text[end + 3:].lstrip("\n")
    fm = yaml.safe_load(raw_fm)
    return fm, body


# ── Index notes ──────────────────────────────────────────────────────────────

def write_game_index(game_dir: Path, guides: list[dict]) -> None:
    guides_sorted = sorted(guides, key=lambda g: g.get("rating", 0) or 0, reverse=True)
    lines = [
        f"# {game_dir.name}",
        "",
        "```dataview",
        'TABLE guide_title AS "Guide", rating AS "★", update_date AS "Updated", unique_visitors AS "Visitors"',
        f'FROM "Steam Guides/{game_dir.name}"',
        "WHERE guide_id != null",
        "SORT rating DESC, unique_visitors DESC",
        "```",
        "",
        "## Guides",
        "",
    ]
    for g in guides_sorted:
        gid = g.get("guide_id", "")
        title = g.get("guide_title", str(gid))
        rating = g.get("rating", "")
        rating_str = f" ★{rating}" if rating else ""
        lines.append(f"- [[{gid}|{title}]]{rating_str}")
    (game_dir / "! index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_global_index(dest: Path, games: list[str]) -> None:
    lines = [
        "# Steam Guides",
        "",
        "```dataview",
        'TABLE WITHOUT ID game_title AS "Game", length(rows) AS "Guides", max(rows.rating) AS "Top Rating"',
        'FROM "Steam Guides"',
        "WHERE game_id != null",
        "GROUP BY game_title",
        "SORT length(rows) DESC",
        "```",
        "",
        "## Games",
        "",
    ]
    for name in sorted(games):
        lines.append(f"- [[{name}/! index|{name}]]")
    (dest / "! Steam Guides.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────

def convert_game(src_dir: Path, game_name: str) -> int:
    dest_dir = DEST / game_name
    dest_dir.mkdir(parents=True, exist_ok=True)

    guides_meta = []
    count = 0

    for src_file in sorted(src_dir.glob("*.md")):
        parsed = parse_guide(src_file)
        if parsed is None:
            continue
        fm, body = parsed
        fm_out = normalize_frontmatter(fm)
        body_out = fix_body(body)

        fm_yaml = yaml.dump(fm_out, allow_unicode=True, sort_keys=False, default_flow_style=False)
        out_text = f"---\n{fm_yaml}---\n\n{body_out}"

        dest_file = dest_dir / src_file.name
        dest_file.write_text(out_text, encoding="utf-8")
        guides_meta.append(fm_out)
        count += 1

    if guides_meta:
        write_game_index(dest_dir, guides_meta)

    return count


def main(games: list[str] | None = None) -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    converted_games = []

    for entry in sorted(SOURCE.iterdir()):
        if entry.name in SKIP or not entry.is_dir():
            continue
        # skip pure-appid folders (all digits)
        if entry.name.isdigit():
            continue
        if games and entry.name not in games:
            continue

        md_files = list(entry.glob("*.md"))
        if not md_files:
            continue

        n = convert_game(entry, entry.name)
        print(f"  {entry.name}: {n} guides")
        converted_games.append(entry.name)

    write_global_index(DEST, converted_games)
    print(f"\nDone — {len(converted_games)} games → {DEST}")

    SB_DEST.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DEST, SB_DEST, dirs_exist_ok=True)
    print(f"Synced → {SB_DEST}")


if __name__ == "__main__":
    # optional: pass game names as args to convert only those
    # e.g. python3 obsidian_convert.py Satisfactory "Dyson Sphere Program"
    target = sys.argv[1:] or None
    main(target)
