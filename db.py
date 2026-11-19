"""SQLite-база доменов: data/domains.db.

Статусы: new -> name_rejected | rejected | scored | dropped.
При повторной встрече домена метрики обновляются; если они изменились — статус
возвращается в new и балл пересчитывается, иначе статус не трогается. first_seen не меняется.

    import db
    conn = db.connect()
    db.import_csv("sample/sample_10pages_v2.csv")     # загрузка CSV без сайта
"""
import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

import filters
from verify_filters import num, split_domain

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "domains.db"

# Колонка сайта -> (колонка базы, число?)
SITE_COLUMNS = {
    "LE": ("le", True), "ACR": ("acr", True), "TR": ("tr", True), "ABY": ("aby", True),
    "WBY": ("wby", True), "TF": ("tf", True), "CF": ("cf", True), "MIP": ("mip", True),
    "BL": ("bl", True), "MDP": ("mdp", True), "MCP": ("mcp", True), "MRDL": ("mrdl", True),
    "ML": ("ml", False), "MRL": ("mrl", False), "TTF": ("ttf", False),
    "Registrar": ("registrar", False), "End Date": ("end_date", False),
    "Status": ("avail_status", False),           # только раздел Deleted: available / …
}
# Не метрики (не вызывают пересчёт): раздел и время удаления (Dropped меняется: Today -> Yesterday).
EXTRA_COLUMNS = {"listing": "TEXT", "dropped_at": "TEXT", "avail_status": "TEXT"}
METRICS = [col for col, _ in SITE_COLUMNS.values()]
SCORE_FIELDS = ["name_score", "name_passed", "name_reason", "name_words", "name_flags",
                "links", "trust", "history", "topic", "base_score", "mult", "score", "flags",
                "status_reason", "scored_at"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS domains (
    domain TEXT PRIMARY KEY,
    domain_display TEXT,
    zone TEXT,
    le REAL, acr REAL, tr REAL, aby REAL, wby REAL, tf REAL, cf REAL, mip REAL,
    bl REAL, mdp REAL, mcp REAL, mrdl REAL,
    ml TEXT, mrl TEXT, ttf TEXT, registrar TEXT, end_date TEXT,
    listing TEXT, dropped_at TEXT, avail_status TEXT,
    first_seen TEXT, last_seen TEXT, scrape_run_id INTEGER,
    name_score REAL, name_passed INTEGER, name_reason TEXT, name_words TEXT, name_flags TEXT,
    links REAL, trust REAL, history REAL, topic REAL, base_score REAL, mult REAL, score REAL,
    flags TEXT, status TEXT DEFAULT 'new', status_reason TEXT, scored_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_domains_status ON domains(status);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT,
    mode TEXT,                 -- full / incremental / from-html / import
    source TEXT,               -- URL поиска, папка HTML или путь CSV
    status TEXT,               -- running / done / failed
    pages INTEGER DEFAULT 0, rows INTEGER DEFAULT 0, new INTEGER DEFAULT 0,
    changed INTEGER DEFAULT 0, known INTEGER DEFAULT 0, duplicates INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0
);
"""


def connect(path=None):
    path = Path(path) if path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(domains)")}
    for col, typ in EXTRA_COLUMNS.items():          # миграция старой базы
        if col not in have:
            conn.execute(f"ALTER TABLE domains ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


def now():
    return datetime.now().isoformat(timespec="seconds")


# ---------- runs ----------

def start_run(conn, mode, source=""):
    cur = conn.execute("INSERT INTO runs (started_at, mode, source, status) VALUES (?, ?, ?, 'running')",
                       (now(), mode, source))
    conn.commit()
    return cur.lastrowid


def update_run(conn, run_id, **fields):
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*fields.values(), run_id))
        conn.commit()


def add_to_run(conn, run_id, **deltas):
    sets = ", ".join(f"{k} = {k} + ?" for k in deltas)
    conn.execute(f"UPDATE runs SET {sets} WHERE id = ?", (*deltas.values(), run_id))
    conn.commit()


def finish_run(conn, run_id, status="done"):
    update_run(conn, run_id, finished_at=now(), status=status)


def unfinished_run(conn, mode, source, day):
    """Незавершённый прогон того же режима и поиска за этот день — для продолжения."""
    return conn.execute(
        "SELECT * FROM runs WHERE status = 'running' AND mode = ? AND source = ? AND started_at LIKE ? "
        "ORDER BY id DESC LIMIT 1", (mode, source, f"{day}%")).fetchone()


# ---------- domains ----------

def site_row_to_record(row, listing="pendingdelete"):
    """Строка таблицы сайта (заголовки как на сайте) -> поля базы. Числа уже распарсены."""
    rec = {}
    for site_col, (col, is_num) in SITE_COLUMNS.items():
        raw = row.get(site_col)
        if is_num:
            rec[col] = num(raw)
        else:
            raw = (raw or "").strip()
            rec[col] = None if raw in ("", "-") else raw
    display = (row.get("Domain") or "").strip()
    rec["domain_display"] = display
    rec["domain"] = display.lower()
    rec["zone"] = row.get("zone") or split_domain(display)[1]
    rec["listing"] = listing
    dropped = (row.get("Dropped") or "").strip()
    rec["dropped_at"] = dropped or None
    return rec


def upsert(conn, rec, run_id, seen_day):
    """Запись одного домена. Возвращает 'new' / 'changed' / 'known'."""
    old = conn.execute("SELECT * FROM domains WHERE domain = ?", (rec["domain"],)).fetchone()
    if old is None:
        cols = ["domain", "domain_display", "zone", *METRICS, "listing", "dropped_at",
                "first_seen", "last_seen", "scrape_run_id", "status"]
        vals = [rec["domain"], rec["domain_display"], rec["zone"], *[rec[m] for m in METRICS],
                rec.get("listing"), rec.get("dropped_at"), seen_day, seen_day, run_id, "new"]
        conn.execute(f"INSERT INTO domains ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        return "new"
    changed = any(old[m] != rec[m] for m in METRICS) or old["domain_display"] != rec["domain_display"]
    last_seen = max(old["last_seen"] or seen_day, seen_day)
    if changed:
        sets = ["domain_display = ?", "zone = ?", *[f"{m} = ?" for m in METRICS],
                "listing = ?", "dropped_at = ?",
                "last_seen = ?", "scrape_run_id = ?", "status = 'new'",
                *[f"{f} = NULL" for f in SCORE_FIELDS]]
        conn.execute(f"UPDATE domains SET {', '.join(sets)} WHERE domain = ?",
                     (rec["domain_display"], rec["zone"], *[rec[m] for m in METRICS],
                      rec.get("listing"), rec.get("dropped_at"), last_seen, run_id, rec["domain"]))
        return "changed"
    conn.execute("UPDATE domains SET last_seen = ?, scrape_run_id = ?, listing = ?, "
                 "dropped_at = COALESCE(?, dropped_at) WHERE domain = ?",
                 (last_seen, run_id, rec.get("listing"), rec.get("dropped_at"), rec["domain"]))
    return "known"


def save_rows(conn, rows, run_id, seen_day, listing="pendingdelete"):
    """Пачка строк одной страницы -> база. Возвращает счётчики."""
    counts = {"new": 0, "changed": 0, "known": 0}
    for row in rows:
        rec = site_row_to_record(row, listing)
        if rec["domain"]:
            counts[upsert(conn, rec, run_id, seen_day)] += 1
    conn.commit()
    return counts


def mark_dropped(conn, today=None):
    """End Date прошла -> dropped."""
    today = (today or date.today()).isoformat()
    cur = conn.execute("UPDATE domains SET status = 'dropped', status_reason = 'End Date прошла' "
                       "WHERE status != 'dropped' AND end_date IS NOT NULL AND end_date < ?", (today,))
    conn.commit()
    return cur.rowcount


def reset_for_rescore(conn):
    """Все не-dropped -> new (после правки порогов в filters.py)."""
    cur = conn.execute(f"UPDATE domains SET status = 'new', "
                       f"{', '.join(f'{f} = NULL' for f in SCORE_FIELDS)} WHERE status != 'dropped'")
    conn.commit()
    return cur.rowcount


def import_csv(path, conn=None):
    """Загрузка domains.csv / sample в базу. Повторный импорт ничего не дублирует."""
    own = conn is None
    conn = conn or connect()
    path = Path(path)
    run_id = start_run(conn, "import", str(path))
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    seen = set()
    totals = {"new": 0, "changed": 0, "known": 0}
    dups = 0
    by_day = {}
    for row in rows:
        key = (row.get("Domain") or "").lower()
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        day = (row.get("scraped_at") or now())[:10]
        by_day.setdefault(day, []).append(row)
    for day, day_rows in by_day.items():
        for k, v in save_rows(conn, day_rows, run_id, day).items():
            totals[k] += v
    update_run(conn, run_id, rows=len(rows), duplicates=dups, **totals)
    finish_run(conn, run_id)
    if own:
        conn.close()
    return {"rows": len(rows), "duplicates": dups, **totals}


def counts_by_status(conn):
    return dict(conn.execute("SELECT status, COUNT(*) FROM domains GROUP BY status").fetchall())


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("Запуск: python db.py <путь>/domains.csv   — импорт CSV в data/domains.db")
    print(import_csv(sys.argv[1]))
