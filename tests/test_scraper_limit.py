import scraper


class FakePage:
    def __init__(self, html):
        self.html = html

    def content(self):
        return self.html


def test_site_page_limit_detected():
    html = '<div class="alert">You have reached the <strong>maximum page limit</strong>.\nYou can use the filter</div>'
    assert scraper.page_limit_reached(FakePage(html))


def test_normal_page_not_limit():
    assert not scraper.page_limit_reached(FakePage("<table><thead><th>Domain</th></thead></table>"))
