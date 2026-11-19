"""Выгрузка результатов в results/:
  top_ГГГГ-ММ-ДД.csv           — лучшие TOP_N по score (UTF-8 с BOM)
  ahrefs_batch_ГГГГ-ММ-ДД.txt  — те же домены по одному в строке (для Ahrefs Batch Analysis)
  report_ГГГГ-ММ-ДД.txt        — сводка: сбор, отсечки, распределение score, топ-20, флаги
"""
import csv
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

import db
import filters as F

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "results"

TOP_COLUMNS = ["rank", "Domain", "score", "links", "trust", "history", "name", "topic", "mult", "flags",
               "MCP", "MDP", "MIP", "TF", "CF", "ABY", "WBY", "ACR", "TTF", "MRL", "Registrar",
               "End Date", "Dropped", "Status", "first_seen"]


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def top_rows(conn, day=None, run_id=None, listing="pendingdelete"):
    """status = scored; при EXCLUDE_DROP_TODAY — только End Date позже дня выгрузки
    (сегодня дропающиеся уже не поймать, даже если их собрали вчера)."""
    day = day or date.today().isoformat()
    where, args = "status = 'scored'", []
    if run_id is not None:            # только домены, собранные этим прогоном
        where += " AND scrape_run_id = ?"
        args.append(run_id)
    if listing == "deleted":          # уже удалены: End Date нет, берём то, что видели в день выгрузки
        where += " AND listing = 'deleted' AND last_seen = ?"
        args.append(day)
    elif F.EXCLUDE_DROP_TODAY:
        where += " AND end_date > ?"
        args.append(day)
    return conn.execute(f"SELECT * FROM domains WHERE {where} "
                        "ORDER BY score DESC, end_date ASC, domain ASC LIMIT ?", (*args, F.TOP_N)).fetchall()


def percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def write_top(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(TOP_COLUMNS)
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["domain_display"], fmt(r["score"]), fmt(r["links"]), fmt(r["trust"]),
                        fmt(r["history"]), fmt(r["name_score"]), fmt(r["topic"]), fmt(r["mult"]), r["flags"] or "",
                        fmt(r["mcp"]), fmt(r["mdp"]), fmt(r["mip"]), fmt(r["tf"]), fmt(r["cf"]), fmt(r["aby"]),
                        fmt(r["wby"]), fmt(r["acr"]), r["ttf"] or "", r["mrl"] or "", r["registrar"] or "",
                        r["end_date"] or "", r["dropped_at"] or "", r["avail_status"] or "",
                        r["first_seen"] or ""])


def build_report(conn, top, day):
    lines = [f"Отчёт {day}", ""]
    run = (conn.execute("SELECT * FROM runs WHERE mode != 'import' ORDER BY id DESC LIMIT 1").fetchone()
           or conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
    if run:
        lines.append(f"Последний прогон #{run['id']} ({run['mode']}, {run['status']}): страниц {run['pages']}, "
                     f"доменов {run['rows']}, новых {run['new']}, изменились {run['changed']}, "
                     f"без изменений {run['known']}, повторов {run['duplicates']}, ошибок {run['errors']}")
    total = conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
    lines.append(f"В базе доменов: {total}")
    lines.append("По статусам: " + ", ".join(f"{k} {v}" for k, v in sorted(db.counts_by_status(conn).items())))

    lines += ["", "Отсечено по причинам:"]
    reasons = Counter()
    for r in conn.execute("SELECT status, status_reason FROM domains "
                          "WHERE status IN ('name_rejected', 'rejected', 'dropped')"):
        # «мало баллов имени (15)» и «(25)» — одна причина
        reasons[(r["status"], re.sub(r"\s*\(\d+\)$", "", r["status_reason"] or ""))] += 1
    lines += [f"  {st}: {why} — {n}" for (st, why), n in reasons.most_common()] or ["  нет"]
    if F.EXCLUDE_DROP_TODAY:
        today_drop = conn.execute("SELECT COUNT(*) FROM domains WHERE status = 'scored' "
                                  "AND COALESCE(listing, 'pendingdelete') = 'pendingdelete' "
                                  "AND (end_date IS NULL OR end_date <= ?)", (day,)).fetchone()[0]
        lines.append(f"  scored, но дроп сегодня ({day}) — не в выгрузке: {today_drop}")

    scores = [r[0] for r in conn.execute("SELECT score FROM domains WHERE status = 'scored'")]
    lines += ["", "Распределение score (все scored):"]
    if scores:
        lines.append(f"  мин {min(scores)}, медиана {round(statistics.median(scores), 1)}, "
                     f"90% {percentile(scores, 0.9)}, макс {max(scores)} (n = {len(scores)})")
    else:
        lines.append("  нет посчитанных доменов")

    lines += ["", f"В выгрузке: {len(top)} (TOP_N = {F.TOP_N})", "", "Топ-20:"]
    for i, r in enumerate(top[:20], 1):
        lines.append(f"  {i:2}. {r['domain_display']:28} {r['score']:5}  MCP {fmt(r['mcp']):>5}  TF {fmt(r['tf']):>3}  "
                     f"{r['flags'] or ''}")

    flags = Counter()
    for (f,) in conn.execute("SELECT flags FROM domains WHERE status = 'scored'"):
        flags.update(x for x in (f or "").split("; ") if x)
    lines += ["", "Флаги (scored):"]
    lines += [f"  {k}: {v}" for k, v in flags.most_common()] or ["  нет"]
    return "\n".join(lines)


def run_listing(conn, run_id):
    row = conn.execute("SELECT source FROM runs WHERE id = ?", (run_id,)).fetchone()
    for name, url in F.LISTINGS.items():
        if row and row["source"] and url in row["source"]:
            return name
    return "pendingdelete"


def export(conn=None, day=None, run_id=None, listing=None, name=None):
    """run_id — выгрузка только по одному прогону: файлы с суффиксом _runN (или _<name>, если задан name).
    listing = "deleted" — файлы top_deleted_*, без условия на End Date."""
    conn = conn or db.connect()
    day = day or date.today().isoformat()
    OUT_DIR.mkdir(exist_ok=True)
    if listing is None:
        listing = run_listing(conn, run_id) if run_id is not None else "pendingdelete"
    prefix = "top_deleted" if listing == "deleted" else "top"
    suffix = f"{day}_{name}" if name else f"{day}_run{run_id}" if run_id is not None else day
    top = top_rows(conn, day, run_id, listing)
    top_path = OUT_DIR / f"{prefix}_{suffix}.csv"
    write_top(top, top_path)
    batch = "ahrefs_batch_deleted" if listing == "deleted" else "ahrefs_batch"
    report_name = "report_deleted" if listing == "deleted" else "report"
    (OUT_DIR / f"{batch}_{suffix}.txt").write_text(
        "".join(r["domain_display"] + "\n" for r in top), encoding="utf-8")
    report = build_report(conn, top, day)
    if run_id is not None:
        report = f"Выгрузка только по прогону #{run_id}\n" + report
    (OUT_DIR / f"{report_name}_{suffix}.txt").write_text(report + "\n", encoding="utf-8")
    files = [top_path, OUT_DIR / f"{batch}_{suffix}.txt", OUT_DIR / f"{report_name}_{suffix}.txt"]
    shown = [str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f) for f in files]
    return {"top": len(top), "files": shown, "report": report}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Выгрузка топа из базы")
    ap.add_argument("--run", type=int, help="только домены, собранные прогоном N (номер — в таблице runs / отчёте)")
    ap.add_argument("--name", help="суффикс файлов вместо runN: --run 19 --name 2 → top_ГГГГ-ММ-ДД_2.csv")
    args = ap.parse_args()
    out = export(run_id=args.run, name=args.name)
    print("Файлы:", ", ".join(out["files"]), f"| доменов в топе: {out['top']}")
