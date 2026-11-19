# PROGRESS

## Фаза 0. Инвентаризация (2026-10-02)

| Файл | Что делает | Работает? |
|---|---|---|
| `scraper.py` | Вход (сессия `state/`, код из почты → Enter), фильтры через GET-URL, листание, `domains/` + `report.txt` + verify | Да — проверено живыми прогонами 2 и 10 стр. Режимов full/incremental, базы и `--from-html` нет |
| `search_form.py` | `filters.py` → параметры формы сайта (зоны по форме, второй уровень .th, группы gTLD/ccTLD), проверка применённых фильтров | Да |
| `verify_filters.py` | Проверка каждой строки CSV по `filters.py`, сводка по правилам, `verify_report.txt` | Да |
| `enable_columns.py` | Включает колонки `filters.COLUMNS` в Column Manager | Да — Registrar включён 2026-10-02 |
| `rank.py` | Прототип ранжирования (MCP-полосы, штрафы) | Импортируется, но устарел — заменяется `score.py` (фаза 5) |
| `filters.py` | Настройки поиска + NAME_* + прототипные константы rank.py | Да, приводится к новому составу в фазе 1 |
| `config.py` | URL, селекторы входа, MAX_PAGES, паузы, пути | Да |
| `config/criteria.yaml`, `config/dropcatch.yaml` | Критерии отбора / площадки заявок | Данные, кодом пока не читались |
| `config/markets.yaml` | Пустой, «ccTLD-рынки добавим позже» | — |
| `sample/sample_10pages.csv` | Исходная выборка 10 стр. (2000 строк, 32 дубля) | только чтение |
| `sample/sample_10pages_v2.csv` | Та же выборка, пересобрана из HTML: ML/MRL/TTF с языками/темами, без дублей (1968) | только чтение |
| `domains/page_001..010.html` | HTML выборки (2026-10-02; фильтры уже новые: длина 17, no Numbers, окно 2 дня; сортировка TF) | Да → перенесено в `sample/pages_2026-10-02/` |

Отдельно:
- `db.py`, `data/domains.db` — **нет**
- режим прироста (incremental) — **нет**
- `run_daily.py` — **нет**
- `logs/` — **нет**
- `tests/` — **нет**
- `name_filter.py` — **нет в корне** (ImportError). `wordlists/` — **нет**. См. DECISIONS.md D1, QUESTIONS.md Q1.

## Фаза 1. filters.py
- Приведён к новому составу; `MAX_PAGES`, паузы перенесены из config.py (config их импортирует). `TR_MAX` оставлен (D3).
- Добавлены `ACR_CAP`, `PREFERRED_/NOT_PREFERRED_/FORBIDDEN_TOPICS` (D6), `HTML_KEEP_DAYS`.
- `rank.py` и его константы удалены (D4). `search_form`: `SORT_BY = "domain"` → `o=domain&r=a`.
- verify_filters уже проверял no Numbers / LENGTH_MAX / ENDS_IN_DAYS_MAX.
- **verify на `sample_10pages_v2.csv`: 1968 строк, нарушений 0.** Ожидались нарушения от старых настроек, но
  выборка 10 стр. собиралась уже с новыми фильтрами (длина 17, no Numbers, 2 дня) — старыми были только прогоны на 2 стр.

## Фаза 2. База — `db.py`, `data/domains.db`
- Таблицы `domains` (все поля из задания + распарсенные числа) и `runs` (+ source, status, changed).
- `upsert`: новый → new; метрики изменились → обновить, status = new, балл сброшен; иначе только last_seen. first_seen не трогается.
- `import_csv()`; `python db.py файл.csv`. Проверка: повторный импорт — new 0 / known 1968.

## Фаза 3. Сборщик
- `scraper.py`: `--full` (всё окно, оценка времени по «Page 1 of N»), обычный (≤ MAX_PAGES), `--from-html ПАПКА`.
- Каждая страница → HTML, CSV, база, runs сразу; продолжение оборванного прогона (D5a). Папки `domains_ГГГГ-ММ-ДД/run_<id>/`,
  удаление старше 7 дней (D5). В конце — runs, verify_filters, report.txt.
- Проверено `--from-html` на 10 страницах: 1968 доменов, 32 повтора пропущены, ML/MRL/TTF разобраны, повтор → 0 новых.
- **Живьём не запускалось** (Q3).

## Фаза 4. Имя в конвейере
- `score.apply_name_filter`: status = new и имя не проверено → `NameFilter().check(domain_display)` → поля name_*;
  не прошёл → name_rejected. Словари — один раз на прогон (~5 с).
- `name_filter.py` восстановлен из чата, словари стартовые (D1, D2, Q1).

## Фаза 5. Общий балл — `score.py`
- Отсечки (мало подсетей / сетка / запрещённая тематика), части links/trust/history/name/topic, base, множители, флаги.
- Пустые значения не роняют расчёт (D7). Проверка: TheSun-подобный = 80.7; MacDisk-подобный → «похоже на сетку», mult 0.6.

## Фаза 6. Выход — `export.py`
- `results/top_*.csv` (BOM, score ↓, End Date ↑, TOP_N), `ahrefs_batch_*.txt`, `report_*.txt`. «Дроп сегодня» — D8.

## Фаза 7. `run_daily.py`
- Этапы сбор → подготовка (dropped, --rescore) → имя → балл → выгрузка; каждый в try/except, трейсбек в `logs/ГГГГ-ММ-ДД.log`.
- Флаги `--full`, `--no-scrape`, `--from-html`, `--import-csv`, `--rescore`, `--max-pages`, `--db` (D9).
- Повторный запуск: имя 0, балл 0, выгрузка та же.

## Фаза 8. Тесты
- `tests/`: числа, name_filter (4 контрольных домена + CamelCase), score (80.7, сетка-флаг, отсечки, пустые значения),
  база (повторный импорт, first_seen), run_daily --no-scrape на пустой базе. **12 passed.**
- Тест нашёл и я исправил: export падал, если папка results вне проекта.

## Фаза 9. Прогон на выборке (`--from-html sample/pages_2026-10-02`, затем `--no-scrape`)
- Собрано 1968 (32 повтора пропущены). Имя: прошли 1829, отсечено 139. Балл: scored 1645, rejected 184.
- **Отсечено по причинам:** сетка 165; имя: мало баллов 67; абракадабра (согласные) 49; запрещённая тематика 19
  (NetPond, GayReign, SeducedArmyBoys …); абракадабра (мало гласных) 16; повторы символов 7.
- **Score (1645 scored):** мин 27.1, медиана 62.5, 90% 70.1, макс 84.7. В выгрузке 860 (785 — дроп в день сбора, D8).
- **Флаги:** перерегистрация 1124, сквозняки 95, TF>>CF 53, мало подсетей 39, мёртвые ссылки 23, похоже на сетку 20, гемблинг 8.

**Топ-15** (среди всех scored; links/trust/history/name/topic, mult):

| # | Домен | score | links | trust | hist | name | topic | mult | MCP | MDP | TF | End | флаги | 1-я тема |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | travelettes.net | 84.7 | 92.7 | 71.4 | 100 | 65 | 100 | 1 | 317 | 612 | 30 | 10-03 | | Recreation/Travel |
| 2 | OutrightUsa.org | 83.3 | 72.4 | 100 | 100 | 65 | 100 | 1 | 90 | 196 | 42 | 10-03 | перерег. | Society/… |
| 3 | TheSun.net | 80.7 | 73.9 | 91.4 | 100 | 70 | 50 | 1 | 99 | 231 | 37 | 10-03 | | News/Media Industry |
| 4 | novinar.net | 79.6 | 84.8 | 60 | 94.5 | 70 | 100 | 1 | 194 | 341 | 26 | 10-02 | | Society/Religion |
| 5 | RudeBusters.com | 78.7 | 75.2 | 80 | 92.2 | 65 | 100 | 1 | 107 | 246 | 33 | 10-02 | | Society/Relationships |
| 6 | MatHiSons.com | 78.5 | 82.5 | 77.1 | 93.4 | 65 | 50 | 1 | 169 | 457 | 32 | 10-03 | | Business |
| 7 | semihsergen.com | 78.2 | 80.4 | 85.7 | 87.2 | 65 | 50 | 1 | 148 | 343 | 35 | 10-03 | перерег. | Arts/Performing Arts |
| 8 | LinkMost.org | 77.9 | 82.3 | 77.1 | 85.7 | 70 | 50 | 1 | 166 | 346 | 32 | 10-02 | перерег. | Regional/Asia |
| 9 | bhsoc.org | 77.6 | 88.7 | 91.4 | 96.2 | 40 | 20 | 1 | 247 | 486 | 37 | 10-02 | | Health |
| 10 | pchelandiya.net | 77.6 | 82.5 | 80 | 93.9 | 65 | 20 | 1 | 168 | 232 | 33 | 10-02 | | Health/Alternative |
| 11 | horariosenespana.com | 77.3 | 86.3 | 57.1 | 93.3 | 60 | 100 | 1 | 214 | 410 | 25 | 10-02 | | Society/Government |
| 12 | BuraSicAnakKale.com | 77.2 | 76.8 | 94.3 | 100 | 45 | 50 | 1 | 118 | 267 | 38 | 10-02 | | Regional/Middle East |
| 13 | visumusa.net | 77.1 | 73.3 | 71.4 | 90.8 | 70 | 100 | 1 | 95 | 240 | 30 | 10-03 | перерег. | Recreation/Travel |
| 14 | FreeCadWeb.org | 76.9 | 100 | 100 | 100 | 65 | 50 | 0.85 | 3600 | 7400 | 59 | 10-02 | сквозняки | Business/Investing |
| 15 | lauferknapp.com | 76.7 | 61.6 | 94.3 | 91.2 | 65 | 100 | 1 | 46 | 126 | 38 | 10-02 | перерег. | Society/Law |

**Контрольные домены** (место среди 1645 scored):
- MacDisk.com — 1470-е, score 52.7 (base 87.8 × 0.6), «похоже на сетку» (MCP 407 / MDP 2300).
- Matt-Thorn.com — 1502-е, 51.2 (base 85.3 × 0.6), «похоже на сетку» (379 / 1500).
- celstumo.com — 1552-е, 48.9 (base 81.5 × 0.6), «похоже на сетку; перерегистрация» (199 / 747).
- NetPond.com — отсечён: «запрещённая тематика» (первая тема Adult/Computers).

**15 случайных отсечённых по имени** (seed 7):
nbdcgs.com — абракадабра (согласные); fjylsw.com — абракадабра (мало гласных); stryjek-www.pl — повторы (www);
charlottesribbbq.com — повторы (bbb) ⚠; cnfgc.com — абракадабра; zzocc.com — мало баллов (15);
ddandnb.com — абракадабра (мало гласных); rvysa.org — мало баллов (25); chlcs.com — абракадабра;
yjnjf.com — мало баллов (15); iaq-hd.de — мало баллов (10); bzyq.net — мало баллов (15); cysys.net — мало баллов (25);
vvd-westerveld.nl — мало баллов (25) ⚠; tycdkj.com — абракадабра (мало гласных). ⚠ — спорные, см. Q4.

## Фаза 10. README
- Установка, .env, команды, где результаты, как менять пороги, что делать при коде из почты, как считается балл.
