"""Одна команда на каждый день: сбор -> имя -> балл -> выгрузка.

    python run_daily.py                       # сбор (MAX_PAGES страниц) и обработка
    python run_daily.py --full                # сбор всего окна до последней страницы
    python run_daily.py --no-scrape           # без сайта: обработать то, что уже в базе
    python run_daily.py --from-html ПАПКА     # вместо сайта разобрать сохранённые page_*.html
    python run_daily.py --import-csv ФАЙЛ     # вместо сайта загрузить CSV (sample и т.п.)
    python run_daily.py --no-scrape --rescore # пересчитать всё после правки filters.py / wordlists

Каждый этап изолирован: ошибка пишется в logs/ГГГГ-ММ-ДД.log с трейсбеком, этап помечается
«упал», следующие работают с тем, что есть в базе. Повторный запуск в тот же день безопасен.
"""
import argparse
import logging
import sys
import time
import traceback
from datetime import date
from pathlib import Path

import db

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"


def setup_log(day):
    LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger("run_daily")
    log.setLevel(logging.INFO)
    if not log.handlers:
        fh = logging.FileHandler(LOG_DIR / f"{day}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    return log


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ежедневный прогон бота дроп-доменов")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--full", action="store_true", help="собрать всё окно (~200 страниц)")
    src.add_argument("--no-scrape", action="store_true", help="пропустить сбор, обработать базу")
    src.add_argument("--from-html", metavar="ПАПКА", help="разобрать сохранённые page_*.html вместо сайта")
    src.add_argument("--import-csv", metavar="ФАЙЛ", help="загрузить CSV вместо сайта")
    ap.add_argument("--max-pages", type=int, help="последняя страница в обычном режиме (по умолчанию MAX_PAGES)")
    ap.add_argument("--start-page", type=int, default=1, help="первая страница: --start-page 21 --max-pages 30")
    ap.add_argument("--listing", choices=["pendingdelete", "deleted"], default="pendingdelete",
                    help="раздел сайта: pendingdelete (по умолчанию) или deleted (Deleted Domains)")
    ap.add_argument("--rescore", action="store_true", help="пересчитать имя и балл у всех (после правки порогов)")
    ap.add_argument("--db", help="путь к базе (по умолчанию data/domains.db)")
    args = ap.parse_args(argv)

    day = date.today().isoformat()
    log = setup_log(day)
    log.info("start %s", " ".join(sys.argv[1:] if argv is None else argv))
    conn = db.connect(args.db)
    results = {}

    def stage(name, fn):
        t0 = time.time()
        try:
            out = fn()
            results[name] = ("ок", out)
            log.info("%s: ок %s (%.1f с)", name, out, time.time() - t0)
        except SystemExit as e:          # понятная остановка (нет .env, не прошёл вход и т.п.)
            results[name] = ("упал", str(e))
            log.error("%s: остановлен: %s", name, e)
        except Exception as e:
            results[name] = ("упал", f"{type(e).__name__}: {e}")
            log.error("%s: упал\n%s", name, traceback.format_exc())
        print(f"[{name}] {results[name][0]}")

    # 1. сбор
    if args.no_scrape:
        results["сбор"] = ("пропущен", "--no-scrape")
    elif args.from_html:
        import scraper
        stage("сбор", lambda: scraper.collect_from_html(args.from_html, conn=conn).splitlines()[0])
    elif args.import_csv:
        stage("сбор", lambda: db.import_csv(args.import_csv, conn=conn))
    else:
        import scraper
        stage("сбор", lambda: scraper.collect(full=args.full, max_pages=args.max_pages, conn=conn,
                                                       start_page=args.start_page, listing=args.listing).splitlines()[0])

    # 2. подготовка: прошедшие дропы, пересчёт по запросу
    def prepare():
        out = {"dropped": db.mark_dropped(conn)}
        if args.rescore:
            out["rescore"] = db.reset_for_rescore(conn)
        return out
    stage("подготовка", prepare)

    # 3–5. имя, балл, выгрузка
    import score
    stage("имя", lambda: score.apply_name_filter(conn))
    stage("балл", lambda: score.apply_scores(conn))

    def do_export():
        import export
        if args.listing == "deleted":       # топ по этому прогону Deleted (top_deleted_*)
            run = conn.execute("SELECT id FROM runs WHERE source LIKE ? ORDER BY id DESC LIMIT 1",
                               (f"%{export.F.LISTINGS['deleted']}%",)).fetchone()
            out = export.export(conn, day, run_id=run["id"] if run else None, listing="deleted")
        else:
            out = export.export(conn, day)
        return {"top": out["top"], "files": out["files"]}
    stage("выгрузка", do_export)

    print("\nИтог:")
    for name in ["сбор", "подготовка", "имя", "балл", "выгрузка"]:
        st, out = results.get(name, ("пропущен", ""))
        print(f"  {name:10} {st:9} {out if st != 'ок' or not isinstance(out, (dict, str)) else out}")
    print("  В базе: " + ", ".join(f"{k} {v}" for k, v in sorted(db.counts_by_status(conn).items())))
    print(f"  Лог: logs/{day}.log")
    problems = sorted((ROOT / "results" / "problems").glob(f"{day}_*.txt"))
    if problems:
        print(f"  Проблемы при сборе ({len(problems)}): results/problems/ — скриншоты и причины")
    log.info("finish %s", {k: v[0] for k, v in results.items()})
    conn.close()
    return 0 if all(v[0] != "упал" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
