import pytest


@pytest.mark.parametrize("domain, ok", [
    ("theblackstripe.com", True),
    ("tjhbw.com", False),
    ("bestcheapdeals.com", False),
    ("hl-design.de", True),
])
def test_name_filter(nf, domain, ok):
    assert nf.check(domain).passed is ok


def test_camelcase_split(nf):
    assert nf.check("FreeCadWeb.org").words == ["free", "cad", "web"]
