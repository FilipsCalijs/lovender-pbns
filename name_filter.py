"""Фильтр по имени домена и балл имени (0–100). Глобальный рынок, без стран.

Работает локально, без сети. Все пороги и баллы берутся из filters.py
(константы NAME_*); если константы там нет — используется значение по умолчанию ниже.

    from name_filter import NameFilter
    nf = NameFilter()
    r = nf.check("GardenTools.com")
    r.passed, r.score, r.words, r.rejected, r.flags
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import tldextract
import wordfreq

try:
    import filters as _F
except ImportError:  # модуль можно проверять и без filters.py
    _F = None

BASE = Path(__file__).parent
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())   # офлайн, встроенный Public Suffix List
VOWELS = set("aeiouy")


def _c(name, default):
    return getattr(_F, name, default) if _F else default


# ---------- настройки (переопределяются в filters.py) ----------
NAME_LANGS = _c("NAME_LANGS", ["en", "de", "es", "pt", "nl", "pl"])
NAME_NO_DICT_ZONES = _c("NAME_NO_DICT_ZONES", ["th", "jp"])  # словари не применяются
NAME_LEN_MIN = _c("NAME_LEN_MIN", _c("LENGTH_MIN", 3))
NAME_LEN_MAX = _c("NAME_LEN_MAX", _c("LENGTH_MAX", 17))
NAME_HYPHENS_MAX = _c("NAME_HYPHENS_MAX", _c("HYPHENS_MAX", 1))
NAME_ALLOW_DIGITS = _c("NAME_ALLOW_DIGITS", False)        # сайт: "no Numbers"
NAME_MAX_CONSONANT_RUN = _c("NAME_MAX_CONSONANT_RUN", 4)  # 5+ подряд -> выброс
NAME_MIN_VOWEL_SHARE = _c("NAME_MIN_VOWEL_SHARE", 0.20)   # для имён от 6 букв
NAME_MAX_SAME_CHAR_RUN = _c("NAME_MAX_SAME_CHAR_RUN", 2)  # aaa -> выброс
NAME_PASS_SCORE = _c("NAME_PASS_SCORE", 30)
NAME_POINTS = _c("NAME_POINTS", {
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
})


def load_list(name: str) -> list[str]:
    path = BASE / "wordlists" / name
    if not path.exists():
        return []
    return [l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def fold(text: str) -> str:
    """Убирает диакритику: łódź -> lodz, café -> cafe."""
    text = text.replace("ł", "l").replace("Ł", "L").replace("ß", "ss")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


class Dictionary:
    """Слово (без диакритики) -> частота zipf. Загружается один раз (~3–5 с)."""

    def __init__(self, langs: list[str]):
        self.words: dict[str, float] = {}
        self.max_len = 2
        for lang in langs:
            if lang not in wordfreq.available_languages():
                continue
            for word, freq in wordfreq.get_frequency_dict(lang).items():
                w = fold(word.lower())
                if not w.isascii() or not w.isalpha() or len(w) < 2 or freq <= 0:
                    continue
                zipf = math.log10(freq) + 9
                if len(w) == 2 and zipf < 5.0:      # редкие двухбуквенные «натягивают» покрытие
                    continue
                if len(w) == 3 and zipf < 3.0:      # редкие трёхбуквенные — чаще аббревиатуры
                    continue
                if not any(ch in VOWELS for ch in w):   # hbw, jdp — аббревиатуры, не слова
                    continue
                if zipf > self.words.get(w, 0):
                    self.words[w] = zipf
                self.max_len = max(self.max_len, len(w))
        for lang in langs:                           # свои словари: wordlists/<lang>.txt
            custom = BASE / "wordlists" / f"{lang}.txt"
            if custom.exists():
                for w in load_list(f"{lang}.txt"):
                    w = fold(w)
                    if w.isalpha():
                        self.words.setdefault(w, 4.0)
                        self.max_len = max(self.max_len, len(w))


def segment(part: str, d: Dictionary) -> tuple[list[str], int]:
    """Лучшая разбивка строки из букв на слова: (слова, покрыто букв)."""
    n, INF = len(part), float("inf")
    best: list[tuple[float, int, list[str]]] = [(INF, 0, [])] * (n + 1)
    best[0] = (0.0, 0, [])
    for i in range(n):
        cost, covered, words = best[i]
        if cost == INF:
            continue
        if cost + 3.0 < best[i + 1][0]:                       # пропуск буквы — дорого
            best[i + 1] = (cost + 3.0, covered, words)
        for j in range(i + 2, min(n, i + d.max_len) + 1):
            zipf = d.words.get(part[i:j])
            if zipf:
                c = cost + 1.0 + (7.0 - min(zipf, 7.0)) * 0.3
                if c < best[j][0]:
                    best[j] = (c, covered + j - i, words + [part[i:j]])
    return best[n][2], best[n][1]


@dataclass
class NameResult:
    domain: str
    name: str = ""
    zone: str = ""
    passed: bool = False
    score: int = 0
    rejected: str = ""           # причина выброса; пусто = прошёл
    coverage: float = 0.0
    words: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


class NameFilter:
    def __init__(self):
        self.dict = Dictionary(NAME_LANGS)
        self.spam = load_list("spam.txt") or [w for w in _c("NAME_BLOCKLIST", [])]
        self.brands = load_list("brands.txt")
        self.mfa = set(load_list("mfa.txt"))
        self.gambling = load_list("gambling.txt")

    @staticmethod
    def split(raw: str) -> tuple[str, str, str, str]:
        """(домен, имя, зона, имя в исходном регистре — для подсказок CamelCase)."""
        domain = raw.strip().rstrip(".")
        domain = re.sub(r"^https?://", "", domain, flags=re.I).split("/")[0]
        if domain.lower().startswith("www."):
            domain = domain[4:]
        original = domain
        domain = domain.lower()
        if "xn--" in domain:
            try:
                domain = domain.encode("ascii").decode("idna")
                original = domain
            except Exception:
                pass
        ext = _EXTRACT(domain)
        orig_name = original[: len(ext.domain)] if original.lower().startswith(ext.domain) else ext.domain
        return domain, ext.domain, ext.suffix, orig_name

    @staticmethod
    def gibberish(name: str) -> str:
        """Абракадабра. Проверяется только для имён, которые НЕ складываются из слов:
        на стыке настоящих слов бывает 5 согласных подряд (black+stripe)."""
        plain = fold(name)
        for run in re.findall(r"[a-z]+", plain):
            cons = re.findall(r"[^aeiouy]+", run)
            if cons and max(map(len, cons)) > NAME_MAX_CONSONANT_RUN:
                return "абракадабра (согласные подряд)"
        letters = re.sub(r"[^a-z]", "", plain)
        if len(letters) >= 6 and sum(ch in VOWELS for ch in letters) / len(letters) < NAME_MIN_VOWEL_SHARE:
            return "абракадабра (мало гласных)"
        return ""

    def hard_reject(self, name: str) -> str:
        plain = fold(name)
        if not NAME_LEN_MIN <= len(name) <= NAME_LEN_MAX:
            return "длина"
        if name.count("-") > NAME_HYPHENS_MAX:
            return "дефисы"
        if not NAME_ALLOW_DIGITS and re.search(r"\d", name):
            return "цифры"
        if re.search(r"(.)\1{%d}" % NAME_MAX_SAME_CHAR_RUN, name):
            return "повторы символов"
        for w in self.spam:
            if w in plain:
                return f"спам-слово: {w}"
        for b in self.brands:
            if b in plain:
                return f"бренд: {b}"
        return ""

    def check(self, raw: str) -> NameResult:
        domain, name, zone, orig = self.split(raw)
        r = NameResult(domain=domain, name=name, zone=zone)
        if not name or not zone:
            r.rejected = "не удалось разобрать домен"
            return r
        reason = self.hard_reject(name)
        if reason:
            r.rejected = reason
            return r

        p = NAME_POINTS
        plain = fold(name)
        top_zone = zone.split(".")[-1]
        use_dict = top_zone not in NAME_NO_DICT_ZONES

        # части имени: по дефисам и по CamelCase из исходного написания (FreeCadWeb -> free|cad|web)
        camel = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", fold(orig).replace("-", " "))
        parts = [c.lower() for c in camel] if any(ch.isupper() for ch in orig) else re.findall(r"[a-z]+", plain)
        letters_total = sum(map(len, parts))

        score = 0
        if use_dict and letters_total:
            words, covered = [], 0
            for part in parts:
                w, cov = segment(part, self.dict)
                words += w
                covered += cov
            r.words = words
            r.coverage = round(covered / letters_total, 2)
            if r.coverage >= 0.999 and len(words) <= 3:
                score += p["coverage_full"]
            elif r.coverage >= 0.8:
                score += p["coverage_80"]
            elif r.coverage >= 0.6:
                score += p["coverage_60"]
            if r.coverage >= 0.6:
                score += p["dict_words"]
        else:
            r.flags.append("без словаря")

        if r.coverage < 0.6:
            reason = self.gibberish(name)
            if reason:
                r.rejected = reason
                return r

        n = len(name)
        score += p["len_8"] if n <= 8 else p["len_12"] if n <= 12 else p["len_17"] if n <= 17 else 0

        if r.coverage < 0.6 and plain.isalpha() and len(plain) >= 4:
            vshare = sum(ch in VOWELS for ch in plain) / len(plain)
            runs = re.findall(r"[^aeiouy]+", plain)
            if 0.3 <= vshare <= 0.6 and (not runs or max(map(len, runs)) <= 3):
                score += p["brandable"]
                r.flags.append("брендабельное")

        if "-" in name:
            score += p["hyphen"]
        mfa = sum(1 for w in r.words if w in self.mfa)
        if mfa == 1:
            score += p["mfa_one"]
        elif mfa >= 2:
            score += p["mfa_many"]
            r.flags.append("MFA")
        if any(g in plain for g in self.gambling):
            r.flags.append("гемблинг")

        r.score = max(0, min(100, score))
        r.passed = r.score >= NAME_PASS_SCORE
        if not r.passed:
            r.rejected = f"мало баллов имени ({r.score})"
        return r


if __name__ == "__main__":
    import sys
    nf = NameFilter()
    for d in sys.argv[1:] or ["GardenTools.com", "kruidenhuis.nl", "bestcheapdeals.com", "xkqzt.com"]:
        r = nf.check(d)
        print(f"{d:28} {'OK' if r.passed else 'NO'} {r.score:3} {' + '.join(r.words)} {r.flags} {r.rejected}")
