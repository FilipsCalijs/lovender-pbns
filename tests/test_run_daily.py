import run_daily


def test_no_scrape_on_empty_db(tmp_path, monkeypatch):
    monkeypatch.setattr(run_daily, "LOG_DIR", tmp_path / "logs")
    import export
    monkeypatch.setattr(export, "OUT_DIR", tmp_path / "results")
    assert run_daily.main(["--no-scrape", "--db", str(tmp_path / "empty.db")]) == 0
    assert (tmp_path / "results").exists()
