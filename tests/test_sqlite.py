from feishu._sqlite import connect


def test_connect_memory_uses_sqlite_memory_database(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    conn = connect(":memory:")
    conn.execute("CREATE TABLE sample (value TEXT)")
    conn.execute("INSERT INTO sample VALUES ('ok')")
    assert conn.execute("SELECT value FROM sample").fetchone()[0] == "ok"
    conn.close()

    assert not (tmp_path / ":memory:").exists()
    assert not (tmp_path / ":memory:-wal").exists()
    assert not (tmp_path / ":memory:-shm").exists()


def test_connect_file_tightens_directory_and_database_permissions(tmp_path):
    db_path = tmp_path / "state" / "tokens.sqlite"

    conn = connect(db_path)
    conn.execute("CREATE TABLE sample (value TEXT)")
    conn.execute("INSERT INTO sample VALUES ('ok')")
    conn.commit()

    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()

    assert db_path.exists()
    assert db_path.parent.stat().st_mode & 0o777 == 0o700
    assert db_path.stat().st_mode & 0o777 == 0o600
