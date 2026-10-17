import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = f"sqlite:///{Path('/tmp/fulfillos-test.db')}"
from app.main import Base, engine, app


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        yield client


def test_health_and_seed(reset_db):
    client = reset_db
    assert client.get("/api/v1/health").status_code == 200
    assert len(client.get("/api/v1/stores").json()) == 3
    assert len(client.get("/api/v1/products").json()) >= 6


def test_store_scoped_reads_reject_unknown_store(reset_db):
    client = reset_db
    assert client.get("/api/v1/stores/missing/inventory").status_code == 404
    assert client.get("/api/v1/stores/missing/inventory/movements").status_code == 404
    assert client.get("/api/v1/stores/missing/forecasts").status_code == 404
    assert client.get("/api/v1/stores/missing/replenishment").status_code == 404
    assert client.get("/api/v1/stores/missing/alerts").status_code == 404


def test_worker_acknowledges_known_outbox_events(reset_db):
    client = reset_db
    order = client.post("/api/v1/orders", json={"delivery_postal_code": "560034", "items": [{"product_id": "prod-bread", "quantity": 1}], "idempotency_key": "worker-demo"}).json()
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from worker.worker import process_once
    from app.main import OutboxEvent, SessionLocal
    assert process_once() >= 1
    with SessionLocal() as db:
        events = db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == order["id"]).all()
        assert events and all(event.status == "processed" for event in events)


def test_order_reserves_stock_and_idempotency(reset_db):
    client = reset_db
    payload = {"delivery_postal_code": "560034", "items": [{"product_id": "prod-milk", "quantity": 2}], "idempotency_key": "demo-order-1"}
    first = client.post("/api/v1/orders", json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "confirmed"
    second = client.post("/api/v1/orders", json=payload)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    inventory = client.get("/api/v1/stores/store-12/inventory").json()
    milk = next(row for row in inventory if row["productId"] == "prod-milk")
    assert milk["availableQuantity"] == 10
    assert milk["reservedQuantity"] == 2


def test_failed_order_and_copilot(reset_db):
    client = reset_db
    response = client.post("/api/v1/orders", json={"delivery_postal_code": "560034", "items": [{"product_id": "prod-milk", "quantity": 1000}]})
    assert response.status_code == 200
    order = response.json()
    assert order["status"] == "failed"
    session = client.post("/api/v1/copilot/sessions").json()
    answer = client.post(f"/api/v1/copilot/sessions/{session['id']}/messages", json={"content": f"Why did order {order['orderNumber']} fail?"})
    assert answer.status_code == 200
    assert "failed" in answer.json()["content"]


def test_copilot_answers_stockout_and_policy_questions_with_citations(reset_db):
    client = reset_db
    client.post("/api/v1/stores/store-12/inventory/consume", json={"product_id": "prod-milk", "quantity": 10, "reason": "Copilot test"})
    session = client.post("/api/v1/copilot/sessions").json()["id"]
    stockout = client.post(f"/api/v1/copilot/sessions/{session}/messages", json={"content": "Which products are at stockout risk?"}).json()
    assert "Full cream milk" in stockout["content"]
    assert stockout["citations"]
    policy = client.post(f"/api/v1/copilot/sessions/{session}/messages", json={"content": "What is the reservation policy?"}).json()
    assert "Reservations reduce available quantity" in policy["content"]
    assert policy["citations"]


def test_forecast_replenishment_and_adjustment(reset_db):
    client = reset_db
    result = client.post("/api/v1/forecasting/run")
    assert result.status_code == 200
    assert result.json()["createdForecasts"] == 18
    recs = client.get("/api/v1/stores/store-12/replenishment").json()
    assert recs
    adjustment = client.post("/api/v1/stores/store-12/inventory/adjust", json={"product_id": "prod-milk", "quantity": 3, "reason": "Cycle count"})
    assert adjustment.status_code == 200
    assert adjustment.json()["movementCreated"] is True


def test_consumption_reduces_available_stock_and_rejects_overconsumption(reset_db):
    client = reset_db
    consumed = client.post("/api/v1/stores/store-12/inventory/consume", json={"product_id": "prod-milk", "quantity": 5, "reason": "Demo sale stream"})
    assert consumed.status_code == 200
    assert consumed.json()["availableQuantity"] == 7
    too_much = client.post("/api/v1/stores/store-12/inventory/consume", json={"product_id": "prod-milk", "quantity": 100, "reason": "Demo sale stream"})
    assert too_much.status_code == 409


def test_approving_replenishment_receives_stock_and_completes_it(reset_db):
    client = reset_db
    client.post("/api/v1/forecasting/run")
    recommendations = client.get("/api/v1/stores/store-12/replenishment").json()
    pending = next(row for row in recommendations if row["status"] == "pending")
    before = next(row for row in client.get("/api/v1/stores/store-12/inventory").json() if row["productId"] == pending["productId"])
    result = client.post(f"/api/v1/replenishment/{pending['id']}/approve")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["receivedQuantity"] == pending["recommendedQuantity"]
    after = next(row for row in client.get("/api/v1/stores/store-12/inventory").json() if row["productId"] == pending["productId"])
    assert after["availableQuantity"] == before["availableQuantity"] + pending["recommendedQuantity"]
    assert any(row["movementType"] == "transfer_in" for row in client.get("/api/v1/stores/store-12/inventory/movements").json())


def test_stockout_alerts_are_actionable_and_forecast_runs_do_not_duplicate_queue(reset_db):
    client = reset_db
    client.post("/api/v1/stores/store-12/inventory/consume", json={"product_id": "prod-milk", "quantity": 10, "reason": "Alert test"})
    alerts = client.get("/api/v1/stores/store-12/alerts")
    assert alerts.status_code == 200
    milk_alert = next(alert for alert in alerts.json() if alert["productId"] == "prod-milk")
    assert milk_alert["severity"] == "critical"
    auto_queue = client.get("/api/v1/stores/store-12/replenishment").json()
    assert any(row["productId"] == "prod-milk" and row["status"] == "pending" for row in auto_queue)
    client.post("/api/v1/forecasting/run")
    first = client.get("/api/v1/stores/store-12/replenishment").json()
    client.post("/api/v1/forecasting/run")
    second = client.get("/api/v1/stores/store-12/replenishment").json()
    assert len([row for row in second if row["status"] == "pending"]) == len([row for row in first if row["status"] == "pending"])
