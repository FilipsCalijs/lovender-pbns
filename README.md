# lovender-pbn-bot — поиск дроп-доменов

Каждый день собирает Pending Delete с expireddomains.net под твоим аккаунтом,
отсеивает плохие имена и профили ссылок, считает балл и выгружает лучшие домены.
Работает без Claude: одна команда, все настройки в `filters.py`, `wordlists/`, `config/`.

## Установка

```
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env     # впиши EXPIREDDOMAINS_LOGIN и EXPIREDDOMAINS_PASSWORD
```

Пароль хранится только в `.env`, в вывод и логи не попадает.

## Команды

| Команда | Что делает |
|---|---|
| `python run_daily.py` | Сбор до `MAX_PAGES` страниц → имя → балл → выгрузка |
| `python run_daily.py --full` | То же, но всё окно до последней страницы (~200 стр., в начале — оценка времени) |
| `python run_daily.py --no-scrape` | Без сайта: обработать то, что уже в базе |
| `python run_daily.py --from-html ПАПКА` | Вместо сайта разобрать сохранённые `page_*.html` |
| `python run_daily.py --import-csv ФАЙЛ` | Вместо сайта загрузить CSV (например `sample/…csv`) |
| `python run_daily.py --no-scrape --rescore` | Пересчитать имя и балл у всех — после правки порогов или словарей |
| `python enable_columns.py` | Включить в Column Manager колонки из `filters.COLUMNS` |
| `python verify_filters.py ФАЙЛ.csv` | Проверить строки CSV на соответствие фильтрам сайта |
| `python -m pytest -q tests` | Тесты |

Повторный запуск в тот же день безопасен: дубли не появляются, посчитанное без изменений не пересчитывается.
Оборванный сбор продолжается со следующей страницы при повторном запуске.

## Где результаты

- `results/top_ГГГГ-ММ-ДД.csv` — лучшие `TOP_N` по score (Excel-совместимый CSV)
- `results/ahrefs_batch_ГГГГ-ММ-ДД.txt` — те же домены по одному в строке, для Ahrefs Batch Analysis
- `results/report_ГГГГ-ММ-ДД.txt` — сколько собрано, отсечено по причинам, распределение score, топ-20, флаги
- `data/domains.db` — база (SQLite): все домены, статусы, баллы; таблица `runs` — история прогонов
- `domains_ГГГГ-ММ-ДД/run_<id>/` — HTML страниц, `domains.csv`, `report.txt`, `verify_report.txt` (хранится 7 дней)
- `logs/ГГГГ-ММ-ДД.log` — лог этапов с трейсбеками ошибок

## Как поменять пороги

Всё в `filters.py`:
- фильтры сайта — `TLDS`, `TF_MIN`, `MDP_MIN`, `LENGTH_MAX`, `ENDS_IN_DAYS_MAX`, …;
- фильтр имени — `NAME_*`; словари — `wordlists/spam.txt`, `brands.txt`, `mfa.txt`, `gambling.txt`;
- отсечки — `MCP_MIN`, `NETWORK_*`; веса балла — `W_*`; множители — `MULT_*`; темы — `PREFERRED_TOPICS`, …;
- размер выгрузки — `TOP_N`.

После правки порогов балла или словарей: `python run_daily.py --no-scrape --rescore`.
После правки фильтров сайта — обычный запуск (они применяются при сборе).

## Код из почты / капча

Скрипт открывает видимое окно Chromium. Если сайт просит код из почты или проверку, в терминале появится
`>>> Нужно твоё действие в браузере…` — сделай его в открытом окне и нажми Enter в терминале (до 3 попыток).
Сессия сохраняется в `state/`, обычно повторный вход не нужен. Капчи и защиту бот не обходит.

## Как считается балл

1. Имя (`name_filter.py`): абракадабра, спам, бренды, повторы → `name_rejected`; иначе балл имени 0–100.
2. Отсечки → `rejected`: MCP < 12 «мало подсетей»; MDP ≥ 500 и MCP/MDP < 0.15 «сетка»; первая тема Adult / Kids «запрещённая тематика».
3. `base = 0.35·links + 0.20·trust + 0.20·history + 0.20·name + 0.05·topic`
   (links — log MCP, trust — TF, history — возраст ABY и ACR, topic — первая тема TTF).
4. Множители профиля: похоже на сетку ×0.6, CF>>TF ×0.7, мёртвые ссылки ×0.7, сквозняки ×0.85, мало подсетей ×0.85, TF>>CF ×0.9.
5. `score = base × mult`. В выгрузку — только `End Date` позже дня сбора.

Заметки о решениях — `DECISIONS.md`, открытые вопросы — `QUESTIONS.md`, ход работ — `PROGRESS.md`.
