from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app import main
from app.main import app
from app.models import Order, OutboxEvent


client = TestClient(app)


class FakeSession:
    def __init__(self, fail_on_flush=False):
        self.added = []
        self.commit_count = 0
        self.rollback_count = 0
        self.fail_on_flush = fail_on_flush

    def add(self, instance):
        self.added.append(instance)

    def flush(self):
        if self.fail_on_flush:
            raise SQLAlchemyError("flush failed")

        order = next(instance for instance in self.added if isinstance(instance, Order))
        order.id = 101
        order.created_at = datetime.utcnow()
        order.updated_at = order.created_at

    def commit(self):
        self.commit_count += 1

    def refresh(self, instance):
        return None

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        return None


def override_db(session):
    def dependency():
        return session

    app.dependency_overrides[main.get_db] = dependency


def clear_db_override():
    app.dependency_overrides.pop(main.get_db, None)


@pytest.fixture
def mock_product_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "get_product",
        lambda product_id: {"id": product_id, "price": 25.0},
    )


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "healthy"
    assert data["service"] == "order-service"


def test_create_order_stores_order_and_outbox_event_in_one_commit(monkeypatch):
    session = FakeSession()
    override_db(session)
    monkeypatch.setattr(main, "get_product", lambda product_id: {"price": 25.0})

    try:
        response = client.post(
            "/orders",
            json={"product_id": 2, "quantity": 3},
        )
    finally:
        clear_db_override()

    assert response.status_code == 200
    order = next(instance for instance in session.added if isinstance(instance, Order))
    outbox_event = next(
        instance for instance in session.added if isinstance(instance, OutboxEvent)
    )

    assert session.commit_count == 1
    assert outbox_event.aggregate_id == order.id == 101
    assert UUID(outbox_event.event_id).version == 4
    assert outbox_event.payload["event_id"] == outbox_event.event_id
    assert outbox_event.payload == {
        "event_id": outbox_event.event_id,
        "event_type": "ORDER_CREATED",
        "order_id": 101,
        "product_id": 2,
        "quantity": 3,
        "price": 25.0,
        "total_amount": 75.0,
        "status": "PENDING",
    }
    assert outbox_event.event_type == "ORDER_CREATED"
    assert outbox_event.aggregate_type == "order"
    assert outbox_event.status == "PENDING"
    assert outbox_event.event_id


def test_create_order_does_not_publish_to_sns(monkeypatch):
    session = FakeSession()
    override_db(session)
    monkeypatch.setattr(main, "get_product", lambda product_id: {"price": 25.0})

    try:
        response = client.post(
            "/orders",
            json={"product_id": 2, "quantity": 3},
        )
    finally:
        clear_db_override()

    assert response.status_code == 200
    assert not hasattr(main, "sns_client")


def test_create_order_rolls_back_when_flush_fails(monkeypatch):
    session = FakeSession(fail_on_flush=True)
    override_db(session)
    monkeypatch.setattr(main, "get_product", lambda product_id: {"price": 25.0})

    try:
        response = client.post(
            "/orders",
            json={"product_id": 2, "quantity": 3},
        )
    finally:
        clear_db_override()

    assert response.status_code == 500
    assert session.commit_count == 0
    assert session.rollback_count == 1
    assert not any(isinstance(instance, OutboxEvent) for instance in session.added)


@pytest.mark.usefixtures("mock_product_service")
def test_create_order():
    response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 3
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["product_id"] == 2
    assert data["quantity"] == 3
    assert data["price"] > 0
    assert data["total_amount"] == data["price"] * 3
    assert data["status"] == "PENDING"

    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


def test_get_orders():
    response = client.get("/orders")

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data, list)


@pytest.mark.usefixtures("mock_product_service")
def test_get_order_by_id():
    create_response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 2
        }
    )

    assert create_response.status_code == 200

    order_id = create_response.json()["id"]

    response = client.get(f"/orders/{order_id}")

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == order_id
    assert data["product_id"] == 2
    assert data["quantity"] == 2
    assert data["price"] > 0
    assert data["total_amount"] == data["price"] * 2


def test_get_order_not_found():
    response = client.get("/orders/999999")

    assert response.status_code == 404

    data = response.json()

    assert data["detail"] == "Order not found"


@pytest.mark.usefixtures("mock_product_service")
def test_update_order():
    create_response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 2
        }
    )

    assert create_response.status_code == 200

    order_id = create_response.json()["id"]

    response = client.put(
        f"/orders/{order_id}",
        json={
            "quantity": 5,
            "status": "CONFIRMED"
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == order_id
    assert data["quantity"] == 5
    assert data["status"] == "CONFIRMED"
    assert data["total_amount"] == data["price"] * 5


def test_update_order_not_found():
    response = client.put(
        "/orders/999999",
        json={
            "quantity": 5,
            "status": "CONFIRMED"
        }
    )

    assert response.status_code == 404

    data = response.json()

    assert data["detail"] == "Order not found"


@pytest.mark.usefixtures("mock_product_service")
def test_delete_order():
    create_response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 1
        }
    )

    assert create_response.status_code == 200

    order_id = create_response.json()["id"]

    response = client.delete(f"/orders/{order_id}")

    assert response.status_code == 200

    data = response.json()

    assert data["message"] == "Order deleted successfully"

    get_response = client.get(f"/orders/{order_id}")

    assert get_response.status_code == 404


def test_delete_order_not_found():
    response = client.delete("/orders/999999")

    assert response.status_code == 404

    data = response.json()

    assert data["detail"] == "Order not found"


def test_create_order_invalid_quantity():
    response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 0
        }
    )

    assert response.status_code == 422


def test_create_order_missing_quantity():
    response = client.post(
        "/orders",
        json={
            "product_id": 2
        }
    )

    assert response.status_code == 422
