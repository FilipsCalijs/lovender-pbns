"""Техника доступа к сайту: адреса, селекторы, сессия. Настройки поиска — в filters.py."""
from filters import MAX_PAGES, PAUSE_MIN, PAUSE_MAX  # noqa: F401 — одно место настроек

LOGIN_URL = "https://www.expireddomains.net/login/"
LISTING_URL = "https://member.expireddomains.net/domains/pendingdelete/#listing"
HEADLESS = False               # окно браузера всегда видимое

# Селекторы формы входа — сняты с реальной страницы LOGIN_URL.
LOGIN_FORM = 'form[action="/logincheck/"]'
LOGIN_INPUT = "#inputLogin"
PASSWORD_INPUT = "#inputPassword"
REMEMBER_CHECKBOX = "#rememberme"
LOGIN_SUBMIT = 'form[action="/logincheck/"] button[type="submit"]'

NEXT_PAGE_SELECTOR = "a.next"  # «Next Page »», проверено по page_001.html

TABLE_WAIT_SECONDS = 20        # сколько ждать появления таблицы
MAX_USER_ATTEMPTS = 3          # сколько раз просить пользователя о помощи
STATE_FILE = "state/storage_state.json"
