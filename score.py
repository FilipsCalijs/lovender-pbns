"""Этапы конвейера над базой: фильтр имени и общий балл. Все пороги — в filters.py.

    python score.py            # имя + балл для всех status = new

Имя: status = new и имя ещё не проверено -> NameFilter().check(domain_display);
     не прошёл -> name_rejected.
Балл: status = new и имя прошло -> отсечки (rejected) или балл (scored).
"""
import math
import re
from datetime import datetime

import db
import filters as F


def clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ---------- имя ----------

def apply_name_filter(conn, nf=None):
    """Проверяет имена у новых доменов. Словари грузятся один раз (nf можно передать готовый)."""
    rows = conn.execute("SELECT domain, domain_display FROM domains "
                        "WHERE status = 'new' AND name_passed IS NULL").fetchall()
    if not rows:
        return {"checked": 0, "passed": 0, "rejected": 0}
    if nf is None:
        from name_filter import NameFilter
        nf = NameFilter()
    passed = 0
    for row in rows:
        r = nf.check(row["domain_display"] or row["domain"])
        status = "new" if r.passed else "name_rejected"
        conn.execute("UPDATE domains SET name_score = ?, name_passed = ?, name_reason = ?, name_words = ?, "
                     "name_flags = ?, status = ?, status_reason = ? WHERE domain = ?",
                     (r.score, int(r.passed), r.rejected, " + ".join(r.words), "; ".join(r.flags),
                      status, None if r.passed else f"имя: {r.rejected}", row["domain"]))
        passed += r.passed
    conn.commit()
    return {"checked": len(rows), "passed": passed, "rejected": len(rows) - passed}


# ---------- балл ----------

def first_topic(ttf):
    """'Arts/Music 43; Business 40' -> 'Arts/Music'."""
    if not ttf:
        return ""
    return re.sub(r"\s+\d+$", "", ttf.split(";")[0].strip())


def topic_matches(topic, prefixes):
    return any(topic == p or topic.startswith(p + "/") for p in prefixes)


def cutoff(d):
    """Причина отсечки или ''."""
    mcp, mdp = d.get("mcp"), d.get("mdp")
    if mcp is not None and mcp < F.MCP_MIN:
        return "мало подсетей"
    if mcp is not None and mdp and mdp >= F.NETWORK_MDP_MIN and mcp / mdp < F.NETWORK_MCP_MDP_MAX:
        return "сетка"
    if topic_matches(first_topic(d.get("ttf")), F.FORBIDDEN_TOPICS):
        return "запрещённая тематика"
    return ""


def compute(d, year):
    """Части балла, base, mult, score и флаги для одного домена (dict с полями базы)."""
    flags = []

    def need(col):
        if d.get(col) is None:
            flags.append(f"нет данных: {col.upper()}")
            return False
        return True

    links = clip(100 * math.log10(d["mcp"]) / math.log10(F.LINKS_MCP_FULL)) if need("mcp") and d["mcp"] >= 1 else 0.0
    trust = clip((d["tf"] - F.TRUST_TF_ZERO) * 100 / (F.TRUST_TF_FULL - F.TRUST_TF_ZERO)) if need("tf") else 0.0

    age = clip((year - d["aby"]) * 100 / F.HISTORY_AGE_FULL) if need("aby") else 0.0
    if need("acr"):
        acr = min(d["acr"], F.ACR_CAP)
        acr_part = clip(100 * math.log10(acr) / math.log10(F.HISTORY_ACR_FULL)) if acr >= 1 else 0.0
    else:
        acr_part = 0.0
    history = F.HISTORY_W_AGE * age + F.HISTORY_W_ACR * acr_part

    name = float(d.get("name_score") or 0)
    topic_name = first_topic(d.get("ttf"))
    if topic_matches(topic_name, F.PREFERRED_TOPICS):
        topic = F.TOPIC_PREFERRED
    elif topic_matches(topic_name, F.NOT_PREFERRED_TOPICS):
        topic = F.TOPIC_NOT_PREFERRED
    else:
        topic = F.TOPIC_NEUTRAL

    base = (F.W_LINKS * links + F.W_TRUST * trust + F.W_HISTORY * history
            + F.W_NAME * name + F.W_TOPIC * topic)

    mult = 1.0
    mcp, mdp, tf, cf = d.get("mcp"), d.get("mdp"), d.get("tf"), d.get("cf")
    if mcp is not None and mdp:
        if mcp / mdp < F.NETWORK_LIKE_RATIO:
            if mdp >= F.NETWORK_MDP_MIN:
                mult *= F.MULT_NETWORK_LIKE
                flags.append("похоже на сетку")
            else:
                mult *= F.MULT_FEW_SUBNETS
                flags.append("мало подсетей")
    if tf and cf is not None:
        if cf / tf > F.CF_TF_HIGH:
            mult *= F.MULT_CF_GT_TF
            flags.append("CF>>TF")
        elif cf / tf < F.CF_TF_LOW:
            mult *= F.MULT_TF_GT_CF
            flags.append("TF>>CF")
    if mdp:
        if d.get("mrdl") is None:
            flags.append("нет данных: MRDL")
        elif d["mrdl"] / mdp < F.LIVE_SHARE_MIN:
            mult *= F.MULT_DEAD_LINKS
            flags.append("мёртвые ссылки")
        if d.get("bl") is None:
            flags.append("нет данных: BL")
        elif d["bl"] / mdp >= F.SITEWIDE_BL_MDP:
            mult *= F.MULT_SITEWIDE
            flags.append("сквозняки")
    if d.get("wby") is not None and d.get("aby") is not None and d["wby"] > d["aby"] + F.REREG_YEARS:
        flags.append("перерегистрация")

    return {"links": round(links, 1), "trust": round(trust, 1), "history": round(history, 1),
            "topic": topic, "base_score": round(base, 1), "mult": round(mult, 4),
            "score": round(base * mult, 1), "flags": flags}


def collection_year(d):
    seen = d.get("last_seen") or d.get("first_seen")
    return int(seen[:4]) if seen else datetime.now().year


def apply_scores(conn):
    rows = conn.execute("SELECT * FROM domains WHERE status = 'new' AND name_passed = 1").fetchall()
    stats = {"scored": 0, "rejected": 0}
    stamp = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        d = dict(row)
        reason = cutoff(d)
        if reason:
            conn.execute("UPDATE domains SET status = 'rejected', status_reason = ?, scored_at = ? WHERE domain = ?",
                         (reason, stamp, d["domain"]))
            stats["rejected"] += 1
            continue
        res = compute(d, collection_year(d))
        flags = res.pop("flags")
        if d.get("name_flags"):
            flags = d["name_flags"].split("; ") + flags
        conn.execute("UPDATE domains SET links = ?, trust = ?, history = ?, topic = ?, base_score = ?, mult = ?, "
                     "score = ?, flags = ?, status = 'scored', status_reason = NULL, scored_at = ? WHERE domain = ?",
                     (res["links"], res["trust"], res["history"], res["topic"], res["base_score"], res["mult"],
                      res["score"], "; ".join(flags), stamp, d["domain"]))
        stats["scored"] += 1
    conn.commit()
    return stats


if __name__ == "__main__":
    conn = db.connect()
    print("Имя:", apply_name_filter(conn))
    print("Балл:", apply_scores(conn))
