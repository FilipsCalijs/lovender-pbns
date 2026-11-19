"""Проверяет каждую строку domains.csv на соответствие filters.py.

Запуск:
    python verify_filters.py domains/domains.csv

Проверки: зона, TF, MDP, MIP, TR, LE, дефисы, цифры, блоклист, ACR, ABY, дни до дропа.
Дни до дропа считаются от даты сбора (scraped_at), а не от сегодняшней.
Для IDN-доменов (xn--) LE с длиной имени не сверяется: сайт считает длину в символах Unicode.
scraper.py вызывает verify() в конце каждого прогона.

Вывод — сводка по правилам и до 5 примеров на правило; полный список
нарушений пишется в verify_report.txt рядом с domains.csv.
"""
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import filters
from search_form import SECOND_LEVEL_FOR

REQUIRED_COLUMNS = ["Domain", "TF", "MDP", "MIP", "TR", "LE", "ACR", "ABY", "End Date"]
# В разделе Deleted нет End Date (домен уже удалён) — вместо неё Dropped, дни до дропа не проверяются.
EXAMPLES_PER_RULE = 5

# Убирают из текста правила то, что меняется от строки к строке, чтобы
# одинаковые нарушения сложились в одну строку сводки.
RULE_PATTERNS = [
    (re.compile(r"^зона \.\S+ "), "зона "),
    (re.compile(r"^дефисов \d+ "), "дефисов "),
    (re.compile(r"^не совпадает с длиной имени .*"), "не совпадает с длиной имени"),
    (re.compile(r"^до дропа -?\d+ дн\., "), "до дропа: "),
]


def num(value):
    """'39.0 K' -> 39000, '1.2 M' -> 1200000, '-' / '' -> None."""
    v = (value or "").strip().replace(",", "")
    if v in ("", "-"):
        return None
    m = re.fullmatch(r"([\d.]+)\s*([KM]?)", v, re.I)
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "K": 1_000, "M": 1_000_000}[m.group(2).upper()]


def zone_allowed(zone):
    tlds = filters.TLDS + filters.CCTLDS
    return zone in tlds or any(zone.endswith("." + t) for t in SECOND_LEVEL_FOR if t in tlds)


def split_domain(domain):
    """(имя без зоны, зона). Зона второго уровня (co.th) определяется, если она разрешена."""
    d = domain.lower()
    parts = d.split(".")
    if len(parts) > 2 and zone_allowed(".".join(parts[-2:])):
        return ".".join(parts[:-2]), ".".join(parts[-2:])
    return ".".join(parts[:-1]), parts[-1]


def check_row(row):
    """Список нарушений строки: (колонка, значение, правило)."""
    out = []
    domain = row.get("Domain", "")
    name, zone = split_domain(domain)
    if not zone_allowed(zone):
        out.append(("Domain", domain, f"зона .{zone} не из TLDS"))

    def at_least(col, minimum):
        v = num(row.get(col))
        if v is None:
            out.append((col, row.get(col, ""), f"нет значения (нужно ≥ {minimum})"))
        elif v < minimum:
            out.append((col, row.get(col), f"< {minimum}"))

    at_least("TF", filters.TF_MIN)
    at_least("MDP", filters.MDP_MIN)
    at_least("MIP", filters.MIP_MIN)
    at_least("ACR", filters.ACR_MIN)

    tr = num(row.get("TR"))
    if tr is not None and tr > filters.TR_MAX:
        out.append(("TR", row.get("TR"), f"> {filters.TR_MAX}"))

    le = num(row.get("LE"))
    if le is None or not filters.LENGTH_MIN <= le <= filters.LENGTH_MAX:
        out.append(("LE", row.get("LE", ""), f"не в диапазоне {filters.LENGTH_MIN}–{filters.LENGTH_MAX}"))
    elif int(le) != len(name) and "xn--" not in name:
        out.append(("LE", row.get("LE"), f"не совпадает с длиной имени «{name}» ({len(name)})"))

    if name.count("-") > filters.HYPHENS_MAX:
        out.append(("Domain", domain, f"дефисов {name.count('-')} > {filters.HYPHENS_MAX}"))
    if filters.NUMBERS_MODE == "no Numbers, except at the end" and re.search(r"\d", name.rstrip("0123456789")):
        out.append(("Domain", domain, "цифры не только в конце"))
    elif filters.NUMBERS_MODE == "no Numbers" and re.search(r"\d", name):
        out.append(("Domain", domain, "есть цифры"))
    for word in filters.NAME_BLOCKLIST:
        if word in name:
            out.append(("Domain", domain, f"содержит запрещённое «{word}»"))

    aby = num(row.get("ABY"))
    if aby is None:
        out.append(("ABY", row.get("ABY", ""), f"нет значения (нужно ≤ {filters.ABY_MAX})"))
    elif aby > filters.ABY_MAX:
        out.append(("ABY", row.get("ABY"), f"> {filters.ABY_MAX}"))

    if "End Date" not in row and "Dropped" in row:
        return out
    end_raw = row.get("End Date", "")
    try:
        end = date.fromisoformat(end_raw[:10])
        scraped = datetime.fromisoformat(row["scraped_at"]).date() if row.get("scraped_at") else date.today()
        days = (end - scraped).days
        if not 0 <= days <= filters.ENDS_IN_DAYS_MAX:
            out.append(("End Date", end_raw, f"до дропа {days} дн., нужно 0–{filters.ENDS_IN_DAYS_MAX}"))
    except ValueError:
        out.append(("End Date", end_raw, "не дата"))
    return out


def rule_key(column, rule):
    for pattern, repl in RULE_PATTERNS:
        rule = pattern.sub(repl, rule)
    return f"{column}: {rule}"


def verify(csv_path):
    """Текст сводки. Полный список нарушений — в verify_report.txt рядом с CSV."""
    csv_path = Path(csv_path)
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        required = [c for c in REQUIRED_COLUMNS if not (c == "End Date" and "Dropped" in header)]
        missing = [c for c in required if c not in header]
        if missing:
            return "verify_filters: Нет колонок: " + ", ".join(missing) + " — строки не проверялись."
        rows = list(reader)

    names = Counter(r.get("Domain", "").lower() for r in rows)
    duplicates = sum(n - 1 for n in names.values() if n > 1)

    violations, passed = [], 0
    for row in rows:
        problems = check_row(row)
        if problems:
            violations += [(row.get("Domain", ""), *p) for p in problems]
        else:
            passed += 1

    by_rule = defaultdict(list)
    for v in violations:
        by_rule[rule_key(v[1], v[3])].append(v)

    lines = [f"verify_filters: строк {len(rows)}, прошли все фильтры {passed}, "
             f"с нарушениями {len(rows) - passed}, дубликатов по Domain {duplicates}"]
    if by_rule:
        ranked = sorted(by_rule.items(), key=lambda kv: -len(kv[1]))
        lines.append("Сводка по правилам (колонка: правило -> нарушений):")
        lines += [f"  {key} -> {len(items)}" for key, items in ranked]
        lines.append(f"Примеры (до {EXAMPLES_PER_RULE} на правило; домен | значение | правило):")
        for key, items in ranked:
            lines.append(f"  [{key}]")
            lines += [f"    {d} | {val} | {rule}" for d, _, val, rule in items[:EXAMPLES_PER_RULE]]
    else:
        lines.append("Нарушений нет.")

    report_path = csv_path.parent / "verify_report.txt"
    full = lines[:1] + ["", "Все нарушения (домен | колонка | значение | правило):"]
    full += ["  " + " | ".join(str(x) for x in v) for v in violations] or ["  нет"]
    report_path.write_text("\n".join(full) + "\n", encoding="utf-8")
    lines.append(f"Полный список: {report_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Запуск: python verify_filters.py <путь>/domains.csv")
    print(verify(sys.argv[1]))
