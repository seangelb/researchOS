"""End-to-end API tests for ResearchOS."""
from __future__ import annotations


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "researchos-api"


def test_empty_positions(client):
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_and_read_position_computes_metrics(client):
    payload = {
        "symbol": "aapl",
        "name": "Apple Inc.",
        "sector": "Technology",
        "shares": 10,
        "cost_basis": 100.0,
        "current_price": 150.0,
        "target_price": 180.0,
        "conviction": "high",
        "thesis": "Test thesis",
    }
    resp = client.post("/api/positions", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["symbol"] == "AAPL"  # normalized to uppercase
    assert body["market_value"] == 1500.0
    assert body["total_cost"] == 1000.0
    assert body["unrealized_gain"] == 500.0
    assert body["unrealized_gain_pct"] == 50.0
    assert body["upside_pct"] == 20.0

    position_id = body["id"]
    resp = client.get(f"/api/positions/{position_id}")
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"


def test_update_position(client):
    created = client.post(
        "/api/positions",
        json={"symbol": "MSFT", "name": "Microsoft", "shares": 5, "cost_basis": 200, "current_price": 200},
    ).json()
    resp = client.patch(f"/api/positions/{created['id']}", json={"current_price": 250})
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_price"] == 250
    assert body["unrealized_gain"] == 250.0  # (250-200)*5


def test_delete_position_cascades_notes(client):
    created = client.post(
        "/api/positions", json={"symbol": "NVDA", "name": "Nvidia"}
    ).json()
    pid = created["id"]
    client.post(f"/api/positions/{pid}/notes", json={"title": "n1", "content": "c1"})
    assert len(client.get(f"/api/positions/{pid}/notes").json()) == 1

    resp = client.delete(f"/api/positions/{pid}")
    assert resp.status_code == 204
    assert client.get(f"/api/positions/{pid}").status_code == 404


def test_notes_flow(client):
    pid = client.post("/api/positions", json={"symbol": "COST", "name": "Costco"}).json()["id"]
    note = client.post(
        f"/api/positions/{pid}/notes", json={"title": "Renewal rates", "content": "93%"}
    ).json()
    assert note["title"] == "Renewal rates"

    notes = client.get(f"/api/positions/{pid}/notes").json()
    assert len(notes) == 1

    resp = client.delete(f"/api/notes/{note['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/positions/{pid}/notes").json() == []


def test_portfolio_summary(client):
    client.post(
        "/api/positions",
        json={"symbol": "AAPL", "name": "Apple", "sector": "Technology", "shares": 10, "cost_basis": 100, "current_price": 150},
    )
    client.post(
        "/api/positions",
        json={"symbol": "XOM", "name": "Exxon", "sector": "Energy", "shares": 20, "cost_basis": 90, "current_price": 100},
    )
    resp = client.get("/api/portfolio/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["positions"] == 2
    assert body["market_value"] == 1500.0 + 2000.0
    assert body["total_cost"] == 1000.0 + 1800.0
    assert body["by_sector"]["Technology"] == 1500.0
    assert body["by_sector"]["Energy"] == 2000.0


def test_missing_position_404(client):
    assert client.get("/api/positions/999").status_code == 404
    assert client.patch("/api/positions/999", json={"name": "x"}).status_code == 404
    assert client.delete("/api/positions/999").status_code == 404
