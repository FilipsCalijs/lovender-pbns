import score


def test_thesun_like():
    d = dict(mcp=99, tf=37, aby=1998, acr=65535, name_score=70, ttf="Business 30")
    r = score.compute(d, 2026)
    assert r["mult"] == 1.0
    assert abs(r["score"] - 80.7) < 0.05


def test_macdisk_like_network_flag():
    d = dict(mcp=407, mdp=2300, tf=37, cf=22, aby=1996, acr=510, name_score=70)
    r = score.compute(d, 2026)
    assert "похоже на сетку" in r["flags"]
    assert r["mult"] == 0.6


def test_cutoffs():
    assert score.cutoff(dict(mcp=11, mdp=40)) == "мало подсетей"
    assert score.cutoff(dict(mcp=200, mdp=2000)) == "сетка"
    assert score.cutoff(dict(mcp=50, mdp=60, ttf="Adult/Computers 45")) == "запрещённая тематика"
    assert score.cutoff(dict(mcp=99, mdp=300, ttf="Sports/Soccer 30")) == ""


def test_missing_values_do_not_crash():
    r = score.compute(dict(name_score=50), 2026)
    assert r["score"] >= 0
    assert "нет данных: MCP" in r["flags"]
    r = score.compute(dict(mcp=50, mdp=0, tf=0, cf=0, aby=None, acr=0, name_score=40), 2026)
    assert r["mult"] == 1.0
