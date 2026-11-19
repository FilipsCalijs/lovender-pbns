"""Все настройки бота — одно место.

scraper.py применяет фильтры сайта, verify_filters.py проверяет по ним собранное,
name_filter.py читает NAME_*, score.py — отсечки, веса и множители, export.py — TOP_N.
"""
from datetime import date

# --- Сбор: фильтры сайта (Pending Delete) ---
# Зоны, которых нет в форме сайта, пропускаются и попадают в отчёт.
TLDS = ["com", "net", "org", "de", "es", "br", "th", "jp", "nl", "pl"]
CCTLDS = []
TF_MIN = 5
MDP_MIN = 15          # Majestic Domain Pop, не DP (SEOkicks)
MIP_MIN = 15
TR_MAX = 2            # Majestic Trust Ratio (CF/TF) — фильтр сайта, см. DECISIONS D3
LENGTH_MIN, LENGTH_MAX = 3, 17
HYPHENS_MAX = 1
NUMBERS_MODE = "no Numbers"
NO_ADULT_NAMES = True
ACR_MIN = 10
ABY_MAX = date.today().year - 2      # год сбора − 2
ENDS_IN_DAYS_MAX = 2
NAME_BLOCKLIST = [
    "viagra", "cialis", "pharma", "pills", "replica", "porn", "xxx", "escort",
    "payday", "hentai", "google", "facebook", "youtube", "instagram", "apple",
    "amazon", "microsoft", "paypal", "netflix", "tiktok",
]
COLUMNS = ["LE", "ABY", "ACR", "WBY", "TF", "CF", "TR", "MDP", "MIP", "MCP",
           "MRDL", "BL", "TTF", "ML", "MRL", "Registrar"]
# Разделы сайта: Pending Delete (ещё не удалены, End Date) и Deleted Domains (уже удалены, Dropped).
LISTINGS = {
    "pendingdelete": "https://member.expireddomains.net/domains/pendingdelete/",
    "deleted": "https://member.expireddomains.net/domains/combinedexpired/",
}
DELETED_NEW_WITHIN = "only new last 48 hours"   # для Deleted вместо «Ends in days»: удалены за 48 ч
SORT_BY = "domain"   # стабильная сортировка: по TF теряется ~15% строк на стыках страниц
MAX_PAGES = 20       # обычный режим; --full — без ограничения
PAUSE_MIN, PAUSE_MAX = 5, 15

# --- Фильтр имени (name_filter.py читает отсюда) ---
NAME_LANGS = ["en", "de", "es", "pt", "nl", "pl"]
NAME_NO_DICT_ZONES = ["th", "jp"]     # словари не применяются
NAME_LEN_MIN, NAME_LEN_MAX = LENGTH_MIN, LENGTH_MAX
NAME_HYPHENS_MAX = HYPHENS_MAX
NAME_ALLOW_DIGITS = False             # сайт: "no Numbers"
NAME_MAX_CONSONANT_RUN = 4            # 5+ согласных подряд -> выброс
NAME_MIN_VOWEL_SHARE = 0.20           # для имён от 6 букв
NAME_MAX_SAME_CHAR_RUN = 2            # aaa -> выброс
NAME_PASS_SCORE = 30
NAME_POINTS = {
    "coverage_full": 40,   # имя целиком из слов, не больше 3 слов
    "coverage_80": 25,
    "coverage_60": 10,
    "dict_words": 15,      # слова из словарей (любого языка)
    "len_8": 15,           # длина имени ≤ 8
    "len_12": 10,          # 9–12
    "len_17": 5,           # 13–17
    "brandable": 10,
    "hyphen": -5,
    "mfa_one": -5,
    "mfa_many": -40,
}

# --- Отсечки перед баллом ---
MCP_MIN = 12                 # MCP < 12 -> «мало подсетей»
NETWORK_MDP_MIN = 500        # MDP ≥ 500 и MCP/MDP < 0.15 -> «сетка»
NETWORK_MCP_MDP_MAX = 0.15
EXCLUDE_DROP_TODAY = True    # в выгрузку только End Date > дата сбора

# --- Общий балл ---
W_LINKS, W_TRUST, W_HISTORY, W_NAME, W_TOPIC = 0.35, 0.20, 0.20, 0.20, 0.05
LINKS_MCP_FULL = 500         # MCP, при котором links = 100
TRUST_TF_ZERO, TRUST_TF_FULL = 5, 40
HISTORY_AGE_FULL = 15        # лет
HISTORY_ACR_FULL = 1000
HISTORY_W_AGE, HISTORY_W_ACR = 0.6, 0.4
ACR_CAP = 65535              # потолок сайта: 65535 = «65535 и больше»
TOPIC_PREFERRED, TOPIC_NEUTRAL, TOPIC_NOT_PREFERRED = 100, 50, 20
# Темы Majestic (префиксы первой темы TTF), сопоставлены с config/criteria.yaml — DECISIONS D6.
PREFERRED_TOPICS = ["Society", "Sports", "Recreation/Travel", "Games"]
NOT_PREFERRED_TOPICS = ["Health", "Recreation/Pets"]
FORBIDDEN_TOPICS = ["Adult", "Kids and Teens"]   # первая тема -> отсечка «запрещённая тематика»

# --- Множители качества профиля ---
MULT_NETWORK_LIKE = 0.6      # MDP ≥ 500 и MCP/MDP < 0.3
NETWORK_LIKE_RATIO = 0.3
MULT_CF_GT_TF = 0.7; CF_TF_HIGH = 1.5
MULT_DEAD_LINKS = 0.7; LIVE_SHARE_MIN = 0.7      # MRDL/MDP
MULT_SITEWIDE = 0.85; SITEWIDE_BL_MDP = 50       # BL/MDP
MULT_FEW_SUBNETS = 0.85                          # MCP/MDP < 0.3, MDP < 500
MULT_TF_GT_CF = 0.9; CF_TF_LOW = 0.2
REREG_YEARS = 1              # WBY > ABY + 1 -> только флаг

# --- Выход ---
TOP_N = 400
HTML_KEEP_DAYS = 7           # папки domains_ГГГГ-ММ-ДД старше — удаляются

# --- На будущее (этап Ahrefs, кода пока нет) ---
AHREFS_DR_MIN = 1; AHREFS_UR_MIN = 1
AHREFS_COLUMNS = ["DR", "UR", "Referring domains", "Referring IPs",
                  "Backlinks", "Organic search"]
