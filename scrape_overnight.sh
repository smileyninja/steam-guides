#!/bin/bash

PYTHON="$HOME/steam-guides/steam-guide-scraper/venv/bin/python"
SCRIPT_PY="$HOME/steam-guides/steam-guide-scraper/steam_guide_scraper.py"
OUT="$HOME/steam-guides"
LOG="$OUT/overnight_scrape.log"
DB="$OUT/steam_picker.db"

echo "Starting scrape: $(date)" | tee -a "$LOG"

if [ ! -f "$DB" ]; then
    echo "No DB found — nothing to scrape" | tee -a "$LOG"
    exit 0
fi

# Get selected, not-yet-downloaded games from DB as "appid|safe_name"
mapfile -t GAMES < <("$PYTHON" - "$DB" <<'PYEOF'
import sys, sqlite3

def safe_dirname(name):
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, '_')
    return name.strip()

conn = sqlite3.connect(sys.argv[1])
rows = conn.execute('SELECT appid, name FROM games WHERE selected=1 AND downloaded=0 ORDER BY playtime DESC').fetchall()
conn.close()
for appid, name in rows:
    print(f'{appid}|{safe_dirname(name)}')
PYEOF
)

if [ ${#GAMES[@]} -eq 0 ]; then
    echo "No games queued for download." | tee -a "$LOG"
    echo "All done: $(date)" | tee -a "$LOG"
    exit 0
fi

echo "Queued: ${#GAMES[@]} games" | tee -a "$LOG"

first=true
for entry in "${GAMES[@]}"; do
    appid="${entry%%|*}"
    name="${entry##*|}"

    count=$(ls "$OUT/$name"/*.md 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        echo "Skipping $name ($count guides already present)" | tee -a "$LOG"
        continue
    fi

    if [ "$first" = false ]; then
        echo "Waiting 15 min before next game..." | tee -a "$LOG"
        sleep 900
    fi
    first=false

    echo "--- $name ($appid) $(date)" | tee -a "$LOG"
    "$PYTHON" "$SCRIPT_PY" \
        -g "$appid" \
        -o "$OUT/$name" \
        --sort-by toprated \
        --limit 25 \
        --delay 20 \
        --retries 3 \
        --timeout 60 \
        >> "$LOG" 2>&1
    echo "Done $name." | tee -a "$LOG"
done

echo "All done: $(date)" | tee -a "$LOG"
