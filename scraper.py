"""Сборщик таблицы Pending Delete с expireddomains.net под аккаунтом пользователя -> data/domains.db.

Запуск (обычно через run_daily.py):
    python scraper.py                    # не больше MAX_PAGES страниц
    python scraper.py --full             # всё окно до последней страницы
    python scraper.py --from-html ПАПКА  # разобрать сохранённые page_*.html без сайта

Каждая страница сразу пишется в базу; оборванный прогон продолжается повторным запуском.
HTML — в domains_ГГГГ-ММ-ДД/run_<id>/, папки старше HTML_KEEP_DAYS удаляются.
Капчи и проверки не обходит — при любой проверке отдаёт управление человеку.
"""
import argparse
import csv
import os
import random
import re
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import config
import db
import filters
import search_form
import verify_filters

ROOT = Path(__file__).resolve().parent


# ---------- таблица ----------

def find_listing_table(soup):
    """Таблица доменов — та, у которой в thead есть колонка «Domain»."""
    for table in soup.find_all("table"):
        thead = table.find("thead")
        if not thead:
            continue
        heads = [th.get_text(" ", strip=True).lower() for th in thead.find_all("th")]
        if any(h.startswith("domain") for h in heads):
            return table
    return None


def language_codes(soup):
    """{'English': 'en', 'Spanish/Castilian': 'es', ...} из списка языков в форме фильтров."""
    sel = soup.find("select", attrs={"name": "fmseoreflang[]"})
    return {o.get_text(strip=True): o["value"] for o in sel.find_all("option")} if sel else {}


def pct(value):
    """'78.00' -> '78%', '0.50' -> '0.5%'."""
    return f"{float(value):g}%"


def lang_cell(td, codes):
    """ML / MRL: 'en 78%; id 6%' — языки с ненулевой долей из title ссылки.
    Без title — код из значка флага и процент из текста."""
    a = td.find("a", title=True)
    if a:
        parts = re.findall(r"([^,(]+?)\s*\(([\d.]+)%\)", a["title"])
        out = [f"{codes.get(name.strip(), name.strip())} {pct(p)}" for name, p in parts if float(p) > 0]
        if out:
            return "; ".join(out)
    flag = td.find("span", title=True)
    text = td.get_text(" ", strip=True)
    return f"{flag['title'].lower()} {text}" if flag and text not in ("", "-") else text


def ttf_cell(td):
    """TTF: 'Arts/Music 43; Arts/Entertainment 27' — тема из title, значение из текста."""
    out = []
    for a in td.find_all("a", title=True):
        topic = re.sub(r"\s*\([\d.]+%\)$", "", a["title"]).strip()
        out.append(f"{topic} {a.get_text(strip=True)}")
    return "; ".join(out) or td.get_text(" ", strip=True)


def cell_value(td, codes):
    classes = td.get("class", [])
    if "field_majesticseo_lang" in classes or "field_majesticseo_reflang" in classes:
        return lang_cell(td, codes)
    if "field_majesticseo_topicaltrustflow" in classes:
        return ttf_cell(td)
    if "field_domain" in classes:
        # при поиске по «Domain contains» сайт подсвечивает слово: s<strong>love</strong>niazg.pl
        a = td.find("a", class_="namelinks")
        if a:
            return a.get("title") or a.get_text("", strip=True)
    # ACR: 65535 — потолок сайта, значит «65535 и больше»; хранится как есть.
    return td.get_text(" ", strip=True)


def parse_table(html):
    """Возвращает (заголовки, строки) из таблицы доменов. Заголовки — из thead."""
    soup = BeautifulSoup(html, "html.parser")
    codes = language_codes(soup)
    table = find_listing_table(soup)
    if table is None:
        return None, []
    header_row = table.find("thead").find_all("tr")[-1]
    headers = []
    for i, th in enumerate(header_row.find_all(["th", "td"])):
        # У пустых заголовков (иконки) имя берём из класса вида head_watchlist.
        css = next((c[5:] for c in th.get("class", []) if c.startswith("head_")), None)
        name = th.get_text(" ", strip=True) or css or f"col_{i + 1}"
        while name in headers:
            name += "_"
        headers.append(name)
    body = table.find("tbody") or table
    rows = []
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        for menu in tr.find_all("ul"):  # скрытые выпадающие меню со ссылками
            menu.decompose()
        values = [cell_value(td, codes) for td in cells]
        values += [""] * (len(headers) - len(values))
        rows.append(dict(zip(headers, values)))
    return headers, rows


def has_table(page):
    return find_listing_table(BeautifulSoup(page.content(), "html.parser")) is not None


def wait_for_table(page, seconds=config.TABLE_WAIT_SECONDS):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if has_table(page):
                return True
        except Exception:
            pass  # страница ещё грузится / идёт навигация
        page.wait_for_timeout(1000)
    return False


# ---------- помощь человека ----------

PROBLEMS_DIR = ROOT / "results" / "problems"


def save_problem(page, reason):
    """Скриншот, HTML и причина в results/problems/ — чтобы было видно, что показал сайт."""
    try:
        PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
        stem = PROBLEMS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S_%f}"[:-3]
        info = [f"Причина: {reason}", f"Время: {datetime.now().isoformat(timespec='seconds')}"]
        if page is not None:
            try:
                info += [f"URL: {page.url}", f"Заголовок: {page.title()}"]
                page.screenshot(path=f"{stem}.png", full_page=True)
                html = page.content()
                Path(f"{stem}.html").write_text(html, encoding="utf-8")
                text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ", strip=True).split())
                info += ["", "Текст страницы (начало):", text[:1500]]
            except Exception as e:  # окно могло уже закрыться
                info.append(f"Скриншот не снят: {type(e).__name__}: {e}")
        Path(f"{stem}.txt").write_text("\n".join(info) + "\n", encoding="utf-8")
        print(f"Проблема сохранена: {stem}.txt" + (" (+ .png, .html)" if page is not None else ""))
    except Exception as e:
        print(f"Не удалось сохранить проблему: {e}")


def ask_user_until_table(page, retry_url):
    """Отдаёт управление пользователю, пока не появится таблица (максимум MAX_USER_ATTEMPTS)."""
    save_problem(page, "сайт не показал таблицу доменов — нужно действие пользователя (код из почты / проверка?)")
    for attempt in range(1, config.MAX_USER_ATTEMPTS + 1):
        print(">>> Нужно твоё действие в браузере (код из почты / проверка).\n"
              "     Сделай его в открытом окне, затем нажми Enter здесь."
              f"  [попытка {attempt}/{config.MAX_USER_ATTEMPTS}]")
        input()
        if wait_for_table(page, 5):
            return True
        # Пользователь мог закончить проверку на другой странице — открываем нужную ещё раз.
        try:
            page.goto(retry_url, wait_until="domcontentloaded")
        except PWTimeout:
            pass
        if wait_for_table(page):
            return True
        print(f"Таблицы доменов всё ещё нет (сейчас открыто: {page.url}).")
    save_problem(page, f"таблица не появилась после {config.MAX_USER_ATTEMPTS} попыток пользователя")
    return False


# ---------- вход ----------

def login(page, user, password):
    page.goto(config.LOGIN_URL, wait_until="domcontentloaded")
    if page.locator(config.LOGIN_FORM).count() == 0:
        print("Форма входа не найдена на странице входа.")
        return
    page.fill(config.LOGIN_INPUT, user)
    page.fill(config.PASSWORD_INPUT, password)
    if page.locator(config.REMEMBER_CHECKBOX).count():
        page.check(config.REMEMBER_CHECKBOX)
    page.click(config.LOGIN_SUBMIT)
    try:
        page.wait_for_load_state("domcontentloaded")
    except PWTimeout:
        pass
    page.wait_for_timeout(2000)


def ensure_logged_in(page, context, user, password):
    print("Открываю список Pending Delete...")
    page.goto(config.LISTING_URL, wait_until="domcontentloaded")
    if wait_for_table(page, 10):
        print("Сессия активна, вход не нужен.")
        return True

    print("Нужен вход, открываю страницу логина...")
    login(page, user, password)

    still_on_login = page.locator(config.LOGIN_FORM).count() > 0
    on_member_site = "member.expireddomains.net" in page.url
    ok = False
    if not still_on_login and on_member_site:
        # Обычный редирект после входа — идём к таблице.
        page.goto(config.LISTING_URL, wait_until="domcontentloaded")
        ok = wait_for_table(page)
    if not ok:
        ok = ask_user_until_table(page, config.LISTING_URL)
    if ok:
        save_state(context)
        print("Вход выполнен, сессия сохранена.")
    return ok


def save_state(context):
    path = ROOT / config.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))


# ---------- сохранение ----------

def append_csv(csv_path, headers, rows, page_no, scraped_at):
    fieldnames = headers + ["page", "scraped_at"]
    is_new = not csv_path.exists() or csv_path.stat().st_size == 0
    if not is_new:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            existing = next(csv.reader(f), [])
        if existing and existing != fieldnames:
            print("Внимание: колонки таблицы отличаются от уже записанных в domains.csv — "
                  "пишу по старому заголовку, новые колонки пропускаются.")
            fieldnames = existing
    # utf-8-sig пишет BOM только в начало нового файла
    with open(csv_path, "a", encoding="utf-8-sig" if is_new else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if is_new:
            w.writeheader()
        for r in rows:
            w.writerow({**r, "page": page_no, "scraped_at": scraped_at})


# ---------- обработка страницы (общая для сайта и --from-html) ----------

def zone_of(domain, zones):
    d = domain.lower()
    return next((z for z in sorted(zones, key=len, reverse=True) if d.endswith("." + z)), "")


class Collector:
    """Принимает HTML страниц по одной: файл, разбор, дубли, CSV, база, runs.
    После каждой страницы всё уже записано — оборванный прогон ничего не теряет."""

    def __init__(self, conn, run_id, out_dir, day, zones, requested=None, listing="pendingdelete"):
        self.conn, self.run_id, self.out_dir, self.day = conn, run_id, out_dir, day
        self.zones = zones
        self.requested = requested           # параметры поиска для сверки применённых фильтров
        self.listing = listing
        self.base = filters.LISTINGS[listing]
        self.csv_path = out_dir / "domains.csv"
        self.problems = []
        self.columns = None
        self.first = []
        # уже встреченные в этом прогоне (при продолжении — из базы)
        self.seen = {r[0] for r in conn.execute("SELECT domain FROM domains WHERE scrape_run_id = ?", (run_id,))}
        self.page_info = None

    def add(self, html, page_no, save_html=True):
        if save_html:
            (self.out_dir / f"page_{page_no:03d}.html").write_text(html, encoding="utf-8")
        if page_no == 1 or self.page_info is None:
            self.page_info = search_form.total_pages(html)
        if self.requested is not None:
            applied = search_form.applied_params(html)
            if applied is None:
                self.problems.append(f"стр. {page_no}: не удалось прочитать применённые фильтры")
            else:
                missing = search_form.missing_params(self.requested, applied)
                if missing:
                    self.problems.append(f"стр. {page_no}: фильтры не встали: {missing}")

        headers, rows = parse_table(html)
        if headers is None:
            self.problems.append(f"стр. {page_no}: нет таблицы доменов")
            db.add_to_run(self.conn, self.run_id, errors=1)
            return 0
        self.columns = self.columns or headers
        fresh, dups = [], 0
        for r in rows:
            key = r.get("Domain", "").lower()
            if key in self.seen:
                dups += 1
                continue
            self.seen.add(key)
            r["zone"] = zone_of(r.get("Domain", ""), self.zones)
            fresh.append(r)
        scraped_at = f"{self.day}T{datetime.now():%H:%M:%S}"
        append_csv(self.csv_path, headers + ["zone"], fresh, page_no, scraped_at)
        counts = db.save_rows(self.conn, fresh, self.run_id, self.day, self.listing)
        db.add_to_run(self.conn, self.run_id, pages=1, rows=len(fresh), duplicates=dups, **counts)
        self.first += [r["Domain"] for r in fresh][: max(0, 10 - len(self.first))]
        print(f"Страница {page_no}: {len(fresh)} доменов (новых {counts['new']}, изменились {counts['changed']}, "
              f"без изменений {counts['known']})" + (f", повторов пропущено {dups}" if dups else ""))
        return len(fresh)

    def finish(self, status, extra_lines=()):
        db.finish_run(self.conn, self.run_id, status)
        run = dict(self.conn.execute("SELECT * FROM runs WHERE id = ?", (self.run_id,)).fetchone())
        verify_text = verify_filters.verify(self.csv_path) if self.csv_path.exists() else "verify_filters: нет domains.csv"
        lines = [f"Прогон #{run['id']} ({run['mode']}) {run['started_at']} – {run['finished_at']}: {status}",
                 *extra_lines,
                 f"Страниц: {run['pages']}" + (f" из {self.page_info}" if self.page_info else ""),
                 f"Доменов: {run['rows']} (новых {run['new']}, изменились {run['changed']}, "
                 f"без изменений {run['known']}), повторов пропущено {run['duplicates']}",
                 "Первые 10: " + ", ".join(self.first)]
        if run["duplicates"] > DUPLICATES_WARN:
            lines.append("ВНИМАНИЕ: много повторов между страницами — часть доменов могла не попасть "
                         "ни на одну страницу. Проверь SORT_BY (должна быть стабильная сортировка).")
        if self.columns:
            missing_cols = [c for c in filters.COLUMNS if c not in self.columns]
            if missing_cols:
                lines.append("Нет колонок из filters.COLUMNS (python enable_columns.py): " + ", ".join(missing_cols))
        lines.append("Проблемы: " + ("; ".join(self.problems) if self.problems else "нет"))
        lines += ["", verify_text]
        text = "\n".join(lines)
        (self.out_dir / "report.txt").write_text(text, encoding="utf-8")
        return text


DUPLICATES_WARN = 5  # больше повторов за прогон — предупреждение в отчёте


def day_dir(day):
    return ROOT / f"domains_{day}"


def cleanup_old_dirs(today):
    """Удаляет папки domains_ГГГГ-ММ-ДД старше HTML_KEEP_DAYS."""
    removed = []
    for d in ROOT.glob("domains_*"):
        m = re.fullmatch(r"domains_(\d{4}-\d{2}-\d{2})", d.name)
        if d.is_dir() and m and (today - date.fromisoformat(m.group(1))).days > filters.HTML_KEEP_DAYS:
            shutil.rmtree(d)
            removed.append(d.name)
    return removed


# ---------- сбор с сайта ----------

def goto_safe(page, url):
    """Переход с одним повтором: сайт иногда обрывает загрузку (net::ERR_ABORTED)."""
    for attempt in (1, 2):
        try:
            page.goto(url, wait_until="commit", timeout=60000)
            return
        except PWError as e:
            if attempt == 2:
                print(f"Переход не удался: {str(e).splitlines()[0]}")
                return
            print("Сайт оборвал загрузку, повтор через 12 с...")
            time.sleep(12)


# Сайт больше ~51 страницы одного поиска не показывает: «You have reached the <strong>maximum page limit</strong>».
SITE_PAGE_LIMIT_RE = re.compile(r"reached the\s*(?:<[^>]+>\s*)*maximum page limit", re.I)


def page_limit_reached(page):
    try:
        return bool(SITE_PAGE_LIMIT_RE.search(page.content()))
    except Exception:
        return False


def scrape_search(page, col, url, start_page, max_pages):
    """Листает один поиск с start_page. Возвращает True, если дошли до конца или до лимита."""
    limit = search_form.page_limit(col.requested)
    goto_safe(page, search_form.url_for(col.requested, (start_page - 1) * limit, col.base) if start_page > 1 else url)
    page_no = start_page
    while True:
        if not wait_for_table(page) and page_limit_reached(page):
            msg = (f"сайт не отдаёт страницы дальше {page_no - 1}-й: «maximum page limit» "
                   f"(≈{(page_no - 1) * limit} строк на один поиск)")
            print(f"Лимит сайта: {msg}. Это конец доступного списка, не ошибка.")
            col.problems.append("лимит сайта: " + msg)
            return True
        if not wait_for_table(page, 1) and not ask_user_until_table(page, page.url):
            print("Таблица так и не появилась — останавливаюсь (прогон можно продолжить повторным запуском).")
            return False
        col.add(page.content(), page_no)
        if page_no == start_page and col.page_info and max_pages is None:
            left = col.page_info - page_no
            avg = (filters.PAUSE_MIN + filters.PAUSE_MAX) / 2 + 3
            print(f"Всего страниц: {col.page_info}. Осталось ~{left * avg / 60:.0f} мин.")
        if max_pages is not None and page_no >= max_pages:
            return True
        nxt = page.locator(config.NEXT_PAGE_SELECTOR).first
        if nxt.count() == 0:
            print("Следующей страницы нет — конец списка.")
            return True
        next_url = nxt.evaluate("a => a.href")
        pause = random.uniform(filters.PAUSE_MIN, filters.PAUSE_MAX)
        time.sleep(pause)
        goto_safe(page, next_url)
        page_no += 1


def collect(full=False, max_pages=None, conn=None, start_page=1, listing="pendingdelete"):
    """Сбор с сайта в базу. full — всё окно; иначе до страницы max_pages (по умолчанию MAX_PAGES).
    start_page > 1 — диапазон страниц (например 21–30): новый прогон, без продолжения старого."""
    load_dotenv(ROOT / ".env")
    user = os.getenv("EXPIREDDOMAINS_LOGIN", "").strip()
    password = os.getenv("EXPIREDDOMAINS_PASSWORD", "")
    if not user or not password:
        raise SystemExit("Заполни EXPIREDDOMAINS_LOGIN и EXPIREDDOMAINS_PASSWORD в файле .env (шаблон — .env.example).")
    mode = "full" if full else "incremental"
    limit_pages = None if full else (max_pages or filters.MAX_PAGES)
    conn = conn or db.connect()
    today = date.today()
    out_dir = day_dir(today.isoformat())
    out_dir.mkdir(exist_ok=True)
    removed = cleanup_old_dirs(today)
    state_path = ROOT / config.STATE_FILE

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        context = browser.new_context(storage_state=str(state_path) if state_path.exists() else None)
        page = context.new_page()
        try:
            if not ensure_logged_in(page, context, user, password):
                save_problem(page, "вход не удался: нет таблицы доменов после входа")
                raise SystemExit("Не удалось попасть на страницу с таблицей доменов после "
                                 f"{config.MAX_USER_ATTEMPTS} попыток. Скриншот — в results/problems/.")
            base = filters.LISTINGS[listing]
            if listing != "pendingdelete":       # форма и ID зон — со страницы нужного раздела
                goto_safe(page, base + "#listing")
                if not wait_for_table(page) and not ask_user_until_table(page, base + "#listing"):
                    raise SystemExit(f"Раздел {listing}: таблица не появилась. Скриншот — в results/problems/.")
                print(f"Раздел: {listing} ({base})")
            start_html = page.content()
            form = search_form.filter_form(BeautifulSoup(start_html, "html.parser"))
            zone_ids, unsupported = search_form.resolve_zones(form, filters.TLDS + filters.CCTLDS)
            print("Зоны: " + ", ".join(zone_ids) + (f" | не поддерживаются сайтом: {', '.join(unsupported)}" if unsupported else ""))
            params, form_problems = search_form.build_params(start_html, zone_ids.values(), listing)
            searches = [("all", list(zone_ids), params)]
            extra = [f"Зоны: {', '.join(zone_ids)}"]
            if unsupported:
                extra.append("Зоны не поддерживаются сайтом: " + ", ".join(unsupported))

            text = ""
            while searches:
                label, zones, sparams = searches.pop(0)
                url = search_form.url_for(sparams, base=base)
                run = db.unfinished_run(conn, mode, url, today.isoformat()) if start_page == 1 else None
                if run:
                    run_id, first_page = run["id"], run["pages"] + 1
                    print(f"Продолжаю прогон #{run_id} со страницы {first_page}.")
                else:
                    run_id, first_page = db.start_run(conn, mode, url), start_page
                    if start_page > 1:
                        print(f"Страницы {start_page}–{limit_pages or 'конец'}.")
                run_dir = out_dir / f"run_{run_id}"
                run_dir.mkdir(exist_ok=True)
                col = Collector(conn, run_id, run_dir, today.isoformat(), zones, sparams, listing)
                col.problems += form_problems
                ok = scrape_search(page, col, url, first_page, limit_pages)
                zone_miss = [pr for pr in col.problems if "ftlds" in pr]
                if label == "all" and zone_miss:
                    print("Сайт не применил все зоны в одном поиске — отдельные прогоны gTLD / ccTLD.")
                    extra.append("Сайт не применил gTLD и ccTLD в одном поиске — прогоны по группам.")
                    for kind, gz in search_form.zone_groups(form, list(zone_ids)).items():
                        gp, _ = search_form.build_params(start_html, [zone_ids[z] for z in gz], listing)
                        searches.append((kind, gz, gp))
                text += col.finish("done" if ok else "failed", [*extra, f"Поиск [{label}]: {url}"]) + "\n\n"
                if not ok:
                    break
            save_state(context)
        except Exception as e:
            save_problem(page, f"сбор упал: {type(e).__name__}: {e}")
            raise
        finally:
            browser.close()
    if removed:
        text += "Удалены старые папки: " + ", ".join(removed)
    print("\n" + text)
    return text


# ---------- разбор сохранённых страниц ----------

def collect_from_html(folder, conn=None):
    """Разбирает сохранённые page_*.html так же, как живые страницы, и пишет в базу."""
    folder = Path(folder)
    pages = sorted(folder.glob("page_*.html"))
    if not pages:
        raise SystemExit(f"В {folder} нет page_*.html")
    m = re.search(r"(\d{4}-\d{2}-\d{2})", folder.name)
    day = m.group(1) if m else datetime.fromtimestamp(pages[0].stat().st_mtime).date().isoformat()
    conn = conn or db.connect()
    run_id = db.start_run(conn, "from-html", str(folder.resolve()))
    out_dir = folder / f"parsed_run_{run_id}"     # CSV и отчёт — рядом с исходными страницами
    out_dir.mkdir(exist_ok=True)
    zones = filters.TLDS + filters.CCTLDS + ["co.th"]
    first_html = pages[0].read_text(encoding="utf-8")
    form = search_form.filter_form(BeautifulSoup(first_html, "html.parser"))
    if form is not None:
        zone_ids, _ = search_form.resolve_zones(form, filters.TLDS + filters.CCTLDS)
        zones = list(zone_ids)
    col = Collector(conn, run_id, out_dir, day, zones)   # без сверки: страницы могли собираться со старыми фильтрами
    for i, path in enumerate(pages, 1):
        col.add(path.read_text(encoding="utf-8"), i, save_html=False)
    text = col.finish("done", [f"Источник: {folder} (дата сбора {day})"])
    print("\n" + text)
    return text


def main():
    ap = argparse.ArgumentParser(description="Сбор Pending Delete в data/domains.db")
    ap.add_argument("--full", action="store_true", help="всё окно до последней страницы")
    ap.add_argument("--max-pages", type=int, help=f"последняя страница в обычном режиме (по умолчанию {filters.MAX_PAGES})")
    ap.add_argument("--start-page", type=int, default=1, help="с какой страницы начать (диапазон: --start-page 21 --max-pages 30)")
    ap.add_argument("--listing", choices=list(filters.LISTINGS), default="pendingdelete", help="раздел сайта")
    ap.add_argument("--from-html", metavar="ПАПКА", help="разобрать сохранённые page_*.html без сайта")
    args = ap.parse_args()
    if args.from_html:
        collect_from_html(args.from_html)
    else:
        collect(full=args.full, max_pages=args.max_pages, start_page=args.start_page, listing=args.listing)


if __name__ == "__main__":
    main()
