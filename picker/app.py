#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import sqlite3
import json
import subprocess
import threading
from pathlib import Path

DB_FILE      = Path.home() / "steam-guides" / "steam_picker.db"
VENV_PY      = Path.home() / "steam-guides/steam-guide-scraper/venv/bin/python"
SCRAPER_PY   = Path.home() / "steam-guides/steam-guide-scraper/steam_guide_scraper.py"
CONVERT_PY   = Path.home() / "steam-guides/obsidian_convert.py"
STEAM_DUMP   = Path.home() / "steam-guides/picker/steam_dump.py"
OUTPUT_DIR   = Path.home() / "steam-guides"
API_KEY_FILE = Path.home() / "steam-guides/.steam_api_key"

app = Flask(__name__)
app.jinja_env.filters["fromjson"] = json.loads

download_jobs = {}  # appid -> {"status": "idle|running|done|error", "msg": ""}
refresh_job   = {"status": "idle", "msg": ""}


# ── DB helpers ────────────────────────────────────────────────────────────────

def query(sql, params=()):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def execute(sql, params=()):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(sql, params)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM games WHERE selected=1").fetchone()[0]
    conn.close()
    return n


def build_filter_sql(tag, genre, dl, q, prefix="WHERE", tag2="", tag3=""):
    clauses, params = [], []
    for t in (tag, tag2, tag3):
        if t:
            clauses.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE value=?)")
            params.append(t)
    if genre:
        clauses.append("EXISTS (SELECT 1 FROM json_each(genres) WHERE value=?)")
        params.append(genre)
    if dl == "yes":
        clauses.append("downloaded=1")
    elif dl == "no":
        clauses.append("downloaded=0")
    if q:
        clauses.append("name LIKE ?")
        params.append(f"%{q}%")
    sql = (f"{prefix} " + " AND ".join(clauses)) if clauses else ""
    return sql, params


# ── Background tasks ──────────────────────────────────────────────────────────

def safe_dirname(name):
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()


def _do_download(appid, name):
    try:
        out = OUTPUT_DIR / safe_dirname(name)
        result = subprocess.run([
            str(VENV_PY), str(SCRAPER_PY),
            "--game-id", str(appid),
            "--output", str(out),
            "--sort-by", "toprated",
            "--limit", "11",
            "--delay", "3.0",
            "--retries", "3",
        ], capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            download_jobs[appid] = {"status": "error", "msg": (result.stderr or result.stdout)[-300:]}
            return
        subprocess.run([str(VENV_PY), str(CONVERT_PY)], capture_output=True, timeout=120)
        subprocess.run([str(VENV_PY), str(STEAM_DUMP), "--sync-fs"], capture_output=True, timeout=30)
        download_jobs[appid] = {"status": "done", "msg": ""}
    except Exception as e:
        download_jobs[appid] = {"status": "error", "msg": str(e)}


def _do_refresh():
    global refresh_job
    key = API_KEY_FILE.read_text().strip() if API_KEY_FILE.exists() else None
    if not key:
        refresh_job = {"status": "error", "msg": "No API key — save one in settings first."}
        return
    try:
        result = subprocess.run(
            [str(VENV_PY), str(STEAM_DUMP), "--api-key", key],
            capture_output=True, text=True, timeout=7200,
        )
        if result.returncode == 0:
            refresh_job = {"status": "done", "msg": "Library refreshed"}
        else:
            refresh_job = {"status": "error", "msg": (result.stderr or result.stdout)[-300:]}
    except Exception as e:
        refresh_job = {"status": "error", "msg": str(e)}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    tag   = request.args.get("tag",   "")
    tag2  = request.args.get("tag2",  "")
    tag3  = request.args.get("tag3",  "")
    genre = request.args.get("genre", "")
    dl    = request.args.get("dl",    "")
    q     = request.args.get("q",     "")

    all_rows = query("SELECT tags, genres FROM games")
    tags_seen, genres_seen = {}, {}
    for row in all_rows:
        for t in json.loads(row["tags"] or "[]"):
            tags_seen.setdefault(t.lower(), t)
        for g in json.loads(row["genres"] or "[]"):
            genres_seen.setdefault(g.lower(), g)
    all_tags   = sorted(tags_seen.values(),   key=str.lower)
    all_genres = sorted(genres_seen.values(), key=str.lower)

    sort  = request.args.get("sort",  "playtime")
    order = request.args.get("order", "desc")
    sort_col_map = {
        "name":       "name COLLATE NOCASE",
        "playtime":   "playtime",
        "downloaded": "downloaded, guide_count",
        "guides":     "guide_count",
    }
    sort_col = sort_col_map.get(sort, "playtime")
    sort_dir = "ASC" if order == "asc" else "DESC"

    where, params = build_filter_sql(tag, genre, dl, q, tag2=tag2, tag3=tag3)
    games = query(f"SELECT * FROM games {where} ORDER BY {sort_col} {sort_dir}", params)
    total          = query("SELECT COUNT(*) AS n FROM games")[0]["n"]
    selected_count = query("SELECT COUNT(*) AS n FROM games WHERE selected=1")[0]["n"]
    has_api_key    = API_KEY_FILE.exists() and bool(API_KEY_FILE.read_text().strip())

    return render_template("index.html",
        games=games,
        all_tags=all_tags, all_genres=all_genres,
        tag=tag, tag2=tag2, tag3=tag3,
        genre=genre, dl=dl, q=q,
        total=total, selected_count=selected_count,
        has_api_key=has_api_key,
        sort=sort, order=order,
    )


@app.route("/toggle/<int:appid>", methods=["POST"])
def toggle(appid):
    rows = query("SELECT selected FROM games WHERE appid=?", (appid,))
    if not rows:
        return jsonify({"error": "not found"}), 404
    new_val = 0 if rows[0]["selected"] else 1
    n = execute("UPDATE games SET selected=? WHERE appid=?", (new_val, appid))
    return jsonify({"selected": new_val, "total_selected": n})


@app.route("/select-visible", methods=["POST"])
def select_visible():
    tag   = request.form.get("tag",   "")
    tag2  = request.form.get("tag2",  "")
    tag3  = request.form.get("tag3",  "")
    genre = request.form.get("genre", "")
    dl    = request.form.get("dl",    "")
    q     = request.form.get("q",     "")
    where, params = build_filter_sql(tag, genre, dl, q, tag2=tag2, tag3=tag3)
    n = execute(f"UPDATE games SET selected=1 {where}", params)
    return jsonify({"total_selected": n})


@app.route("/clear-all", methods=["POST"])
def clear_all():
    execute("UPDATE games SET selected=0")
    return jsonify({"total_selected": 0})


@app.route("/download/<int:appid>", methods=["POST"])
def download(appid):
    if download_jobs.get(appid, {}).get("status") == "running":
        return jsonify({"status": "running"})
    rows = query("SELECT name FROM games WHERE appid=?", (appid,))
    if not rows:
        return jsonify({"error": "not found"}), 404
    download_jobs[appid] = {"status": "running", "msg": ""}
    threading.Thread(target=_do_download, args=(appid, rows[0]["name"]), daemon=True).start()
    return jsonify({"status": "running"})


@app.route("/job-status/<int:appid>")
def job_status(appid):
    job = dict(download_jobs.get(appid, {"status": "idle", "msg": ""}))
    if job["status"] == "done":
        rows = query("SELECT guide_count, downloaded FROM games WHERE appid=?", (appid,))
        if rows:
            job["guide_count"] = rows[0]["guide_count"]
    return jsonify(job)


@app.route("/refresh-library", methods=["POST"])
def refresh_library():
    global refresh_job
    if refresh_job.get("status") == "running":
        return jsonify(refresh_job)
    refresh_job = {"status": "running", "msg": "Fetching from Steam…"}
    threading.Thread(target=_do_refresh, daemon=True).start()
    return jsonify(refresh_job)


@app.route("/refresh-status")
def refresh_status_route():
    return jsonify(refresh_job)


@app.route("/save-api-key", methods=["POST"])
def save_api_key():
    key = request.form.get("key", "").strip()
    if not key:
        return jsonify({"error": "empty"}), 400
    API_KEY_FILE.write_text(key)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
