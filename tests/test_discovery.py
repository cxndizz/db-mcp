from oracle_mcp.discovery import _merge, parse_dbeaver, parse_sqldev

# Shapes mirror the real DBeaver data-sources.json / SQL Developer connections.json.

DBEAVER = {
    "connections": {
        "oracle_thin-aaa": {
            "provider": "oracle", "driver": "oracle_thin", "name": "Oracle - Prod",
            "configuration": {
                "host": "oradb.example", "port": "1553", "database": "oraapps",
                "url": "jdbc:oracle:thin:@oradb.example:1553:oraapps",
                "provider-properties": {"@dbeaver-sid-service@": "SID"},
            },
        },
        "oracle_thin-bbb": {
            "provider": "oracle", "driver": "oracle_thin", "name": "UAT1",
            "configuration": {
                "host": "203.0.113.5", "port": "1521", "database": "UAT1",
                "url": "jdbc:oracle:thin:@//203.0.113.5:1521/UAT1",
                "provider-properties": {"@dbeaver-sid-service@": "SERVICE"},
            },
        },
        "pg-1": {"provider": "postgresql", "name": "pg", "configuration": {}},
    }
}

SQLDEV = {
    "connections": [
        {"name": "PROD", "type": "jdbc", "info": {
            "subtype": "oraJDBC", "RaptorConnectionType": "Oracle",
            "hostname": "oradb.example", "port": "1553", "sid": "oraapps",
            "user": "CHAKKRIA", "customUrl": "jdbc:oracle:thin:@oradb.example:1553:oraapps"}},
    ]
}


def test_parse_dbeaver_only_oracle():
    conns = parse_dbeaver(DBEAVER)
    assert len(conns) == 2  # postgres skipped
    prod = next(c for c in conns if "Oracle - Prod" in c.names)
    assert prod.host == "oradb.example"
    assert prod.port == 1553
    assert prod.db_name == "oraapps"
    assert prod.is_service is False
    assert prod.username is None  # DBeaver has no username here
    uat = next(c for c in conns if "UAT1" in c.names)
    assert uat.is_service is True


def test_parse_sqldev_has_username():
    conns = parse_sqldev(SQLDEV)
    assert len(conns) == 1
    assert conns[0].username == "CHAKKRIA"
    assert conns[0].host == "oradb.example"
    assert conns[0].is_service is False


def test_merge_dedupes_and_fills_username():
    merged = _merge(parse_dbeaver(DBEAVER) + parse_sqldev(SQLDEV))
    # Prod appears in both apps -> one merged entry with the username from sqldev.
    prod = next(c for c in merged if c.host == "oradb.example")
    assert prod.sources == {"dbeaver", "sqldev"}
    assert prod.username == "CHAKKRIA"
    # UAT1 only in DBeaver.
    ids = {c.id for c in merged}
    assert "oradb.example:1553:oraapps" in ids
    assert "203.0.113.5:1521/UAT1" in ids


def test_connection_id_and_matching():
    prod = parse_sqldev(SQLDEV)[0]
    assert prod.id == "oradb.example:1553:oraapps"
    assert prod.engine == "oracle"
    assert prod.matches("PROD")
    assert prod.matches("oradb.example:1553:oraapps")
    assert not prod.matches("nope")


def test_server_connections_postgres(tmp_path, monkeypatch):
    import json
    from oracle_mcp.discovery import server_connections

    cfg = tmp_path / "conns.json"
    cfg.write_text(json.dumps([
        {"engine": "postgres", "host": "127.0.0.1", "port": 5432,
         "db_name": "sandbox", "username": "mcp_ro", "name": "pg-sandbox"},
    ]), encoding="utf-8")
    monkeypatch.setenv("DB_MCP_CONNECTIONS", str(cfg))

    conns = server_connections()
    assert len(conns) == 1
    c = conns[0]
    assert c.engine == "postgres"
    assert c.is_service is True  # postgres is service-style
    assert c.id == "127.0.0.1:5432/sandbox"
    assert c.username == "mcp_ro"
    assert c.sources == {"config"}
