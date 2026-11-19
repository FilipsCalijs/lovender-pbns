import csv

import db

ROWS = [
    {"Domain": "ExampleOne.com", "TF": "20", "MDP": "1.1 K", "MCP": "300", "End Date": "2026-10-03",
     "scraped_at": "2026-10-02T10:00:00", "zone": "com"},
    {"Domain": "second.de", "TF": "15", "MDP": "40", "MCP": "30", "End Date": "2026-10-03",
     "scraped_at": "2026-10-02T10:00:00", "zone": "de"},
]


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_reimport_no_duplicates_and_first_seen_kept(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    src = tmp_path / "a.csv"
    write_csv(src, ROWS)
    assert db.import_csv(src, conn)["new"] == 2
    again = db.import_csv(src, conn)
    assert again["new"] == 0 and again["known"] == 2
    assert conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0] == 2

    later = [dict(ROWS[0], TF="25", scraped_at="2026-10-04T09:00:00")]
    write_csv(tmp_path / "b.csv", later)
    assert db.import_csv(tmp_path / "b.csv", conn)["changed"] == 1
    row = conn.execute("SELECT * FROM domains WHERE domain = 'exampleone.com'").fetchone()
    assert row["first_seen"] == "2026-10-02"
    assert row["last_seen"] == "2026-10-04"
    assert row["tf"] == 25 and row["mdp"] == 1100
    assert row["domain_display"] == "ExampleOne.com"
    assert row["status"] == "new"
