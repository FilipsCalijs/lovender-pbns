"""Переводит filters.py в параметры поиска expireddomains.net по реальной форме на странице.

Имена полей сняты с формы Pending Delete (2026-10-02). Значения, которые зависят
от сайта (ID зон, чекбокс режима цифр, максимум строк на странице), берутся
из HTML открытой страницы, а не хардкодятся.
"""
import re
from urllib.parse import urlencode, parse_qsl, urlsplit

from bs4 import BeautifulSoup

import filters

BASE = "https://member.expireddomains.net/domains/pendingdelete/"

# Для этих зон включаются и все зоны второго уровня, которые сайт предлагает отдельно
# (по заданию: .th → co.th, in.th и т.п.).
SECOND_LEVEL_FOR = ["th"]

# Колонка для SORT_BY -> (параметр o=, направление r=) — из ссылок сортировки в thead.
SORT_PARAMS = {"domain": ("domain", "a"), "TF": ("majesticseo_tf", "d"), "CF": ("majesticseo_cf", "d"),
               "MDP": ("majesticseo_domainpop", "d"), "MIP": ("majesticseo_ippop", "d"),
               "ACR": ("aentries", "d"), "BL": ("bl", "d"), "TR": ("majesticseo_tr", "d")}

# Field -> значение из filters.py. Названия полей — из формы (раздел в комментарии).
STATIC_FIELDS = [
    ("fmseotf", filters.TF_MIN),                 # Majestic → Trust Flow (от)
    ("fmseorefdomains", filters.MDP_MIN),        # Majestic → Domain Pop (от)
    ("fmseorefips", filters.MIP_MIN),            # Majestic → IP Pop (от)
    ("fmseotrmax", filters.TR_MAX),              # Majestic → Trust Ratio (до)
    ("fminhost", filters.LENGTH_MIN),            # Domain Name Settings → Length
    ("fmaxhost", filters.LENGTH_MAX),
    ("fmaxhyphen", filters.HYPHENS_MAX),         # Domain Name Settings → Hyphens (до)
    ("facr", filters.ACR_MIN),                   # Common SEO → ACR (от)
    ("fabirth_year", filters.ABY_MAX),           # Common SEO → ABY («- max -»)
    ("fenddaysmax", filters.ENDS_IN_DAYS_MAX),   # Listing Settings → Ends in days (до)
    ("fdomainnot", " ".join(filters.NAME_BLOCKLIST)),  # Domain Name Blocklist → contains not
]


def filter_form(soup):
    return next((f for f in soup.find_all("form") if f.find(attrs={"name": "button_submit"})), None)


def zone_options(form):
    """{'com': '2', 'co.th': '355', ...} — все зоны, которые можно выбрать в форме."""
    out = {}
    for box in form.find_all("input", attrs={"name": "ftlds[]"}):
        label = box.find_parent("label")
        if label:
            out[label.get_text(strip=True).lower()] = box["value"]
    return out


def zone_groups(form, zones):
    """Делит зоны по разделам формы: {'gTLD': [...], 'ccTLD': [...]}. Нужен, если сайт
    не применяет gTLD и ccTLD в одном поиске."""
    groups = {}
    for box in form.find_all("input", attrs={"name": "ftlds[]"}):
        label = box.find_parent("label")
        zone = label.get_text(strip=True).lower() if label else None
        if zone in zones:
            heading = box.find_previous("h3")
            kind = "gTLD" if heading and "gTLD" in heading.get_text() else "ccTLD"
            groups.setdefault(kind, []).append(zone)
    return groups


def resolve_zones(form, tlds):
    """(выбранные зоны, неподдерживаемые). Второй уровень — для SECOND_LEVEL_FOR."""
    options = zone_options(form)
    chosen, unsupported = [], []
    for tld in tlds:
        if tld in options:
            chosen.append(tld)
        elif tld not in SECOND_LEVEL_FOR:
            unsupported.append(tld)
        if tld in SECOND_LEVEL_FOR:
            subs = sorted(z for z in options if z.endswith("." + tld))
            chosen += subs
            if tld not in options and not subs:
                unsupported.append(tld)
    return {z: options[z] for z in chosen}, unsupported


def checkbox_by_label(form, text):
    for box in form.find_all("input", type="checkbox"):
        label = (form.find("label", attrs={"for": box["id"]}) if box.get("id") else None) or box.find_parent("label")
        if label and label.get_text(" ", strip=True).lower() == text.lower():
            return box["name"], box.get("value", "1")
    return None


def build_params(page_html, zone_ids, listing="pendingdelete"):
    """Список пар (поле, значение) для GET-запроса и список проблем с формой.
    listing = "deleted": вместо «Ends in days» — чекбокс DELETED_NEW_WITHIN (удалённые уже не «заканчиваются»)."""
    soup = BeautifulSoup(page_html, "html.parser")
    form = filter_form(soup)
    problems = []
    order, direction = SORT_PARAMS[filters.SORT_BY]
    params = [("o", order), ("r", direction)]
    params += [("ftlds[]", zid) for zid in zone_ids]
    for name, value in STATIC_FIELDS:
        if listing == "deleted" and name == "fenddaysmax":
            continue
        if form.find(attrs={"name": name}) is None:
            problems.append(f"в форме нет поля {name}")
        else:
            params.append((name, str(value)))
    numbers = checkbox_by_label(form, filters.NUMBERS_MODE)
    if numbers:
        params.append(numbers)
    else:
        problems.append(f"в форме нет чекбокса «{filters.NUMBERS_MODE}»")
    if filters.NO_ADULT_NAMES:
        adult = checkbox_by_label(form, "no Adult Names")
        if adult:
            params.append(adult)
        else:
            problems.append("в форме нет чекбокса «no Adult Names»")
    if listing == "deleted":
        window = checkbox_by_label(form, filters.DELETED_NEW_WITHIN)
        if window:
            params.append(window)
        else:
            problems.append(f"в форме нет чекбокса «{filters.DELETED_NEW_WITHIN}»")
    limit = form.find("select", attrs={"name": "flimit"})
    if limit:
        params.append(("flimit", str(max(int(o["value"]) for o in limit.find_all("option")))))
    return params, problems


def url_for(params, start=0, base=BASE):
    """GET-адрес поиска; start — смещение в строках (страница N = (N-1) × flimit)."""
    if start:
        params = list(params) + [("start", str(start))]
    return base + "?" + urlencode(params) + "#listing"


def page_limit(params):
    return int(dict(params).get("flimit", 25))


def applied_params(page_html):
    """Фильтры, которые сайт реально применил: берутся из ссылки сортировки по TF в thead."""
    soup = BeautifulSoup(page_html, "html.parser")
    a = soup.select_one("th.head_majesticseo_tf a[href]") or soup.select_one("table thead th a[href*='?']")
    if not a:
        return None
    return [(k, v) for k, v in parse_qsl(urlsplit(a["href"]).query, keep_blank_values=True)
            if k not in ("o", "r", "start")]


def missing_params(requested, applied):
    """Какие из запрошенных (поле, значение) сайт не применил."""
    have = set(applied)
    return [(k, v) for k, v in requested if k not in ("o", "r", "start") and (k, v) not in have]


def total_pages(page_html):
    """Число страниц из «Page 1 of N»; None, если сайт его не показал."""
    info = BeautifulSoup(page_html, "html.parser").select_one("div.pageinfo")
    m = re.search(r"of\s+([\d,]+)", info.get_text(" ", strip=True)) if info else None
    return int(m.group(1).replace(",", "")) if m else None
