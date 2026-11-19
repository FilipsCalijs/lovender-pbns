"""Включает в Column Manager аккаунта колонки из filters.COLUMNS, которых там нет.

Только добавляет — уже выбранные колонки не снимает и порядок не меняет.
Запуск: python enable_columns.py
"""
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

import config
import filters

URL = "https://member.expireddomains.net/account/columnmanager/"


def selected_columns(html):
    """{'TF': ('majesticseo_tf', True), ...} по подписи «ABBR - описание»."""
    sel = BeautifulSoup(html, "html.parser").find("select", attrs={"name": "viewfields[]"})
    out = {}
    for o in sel.find_all("option"):
        abbr = o.get_text(strip=True).split(" - ")[0]
        out[abbr] = (o["value"], o.has_attr("selected"))
    return out


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=config.HEADLESS)
        context = browser.new_context(storage_state=config.STATE_FILE)
        page = context.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            cols = selected_columns(page.content())
            unknown = [c for c in filters.COLUMNS if c not in cols]
            to_add = [cols[c][0] for c in filters.COLUMNS if c in cols and not cols[c][1]]
            if unknown:
                print("Нет таких колонок в Column Manager:", ", ".join(unknown))
            if not to_add:
                print("Все колонки из filters.COLUMNS уже включены.")
                return
            page.evaluate("""(values) => {
                const sel = document.querySelector('select[name="viewfields[]"]');
                for (const o of sel.options) if (values.includes(o.value)) o.selected = true;
            }""", to_add)
            page.click('button[name="save"]')
            page.wait_for_load_state("domcontentloaded")
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            after = selected_columns(page.content())
            ok = [c for c in filters.COLUMNS if c in after and after[c][1]]
            missing = [c for c in filters.COLUMNS if c in after and not after[c][1]]
            print("Включены:", ", ".join(ok))
            print("Не включились:", ", ".join(missing) or "нет")
            context.storage_state(path=config.STATE_FILE)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
