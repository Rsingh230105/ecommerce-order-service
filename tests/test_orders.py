from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "healthy"
    assert data["service"] == "order-service"


def test_create_order():
    response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 3,
            "price": 55000
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["product_id"] == 2
    assert data["quantity"] == 3
    assert data["price"] == 55000
    assert data["total_amount"] == 165000
    assert data["status"] == "PENDING"

    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


def test_get_orders():
    response = client.get("/orders")

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data, list)


def test_get_order_by_id():
    # Create an order first
    create_response = client.post(
        "/orders",
        json={
            "product_id": 10,
            "quantity": 2,
            "price": 1000
        }
    )

    assert create_response.status_code == 200

    order_id = create_response.json()["id"]

    # Get the created order
    response = client.get(f"/orders/{order_id}")

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == order_id
    assert data["product_id"] == 10
    assert data["quantity"] == 2
    assert data["price"] == 1000
    assert data["total_amount"] == 2000


def test_get_order_not_found():
    response = client.get("/orders/999999")

    assert response.status_code == 404

    data = response.json()

    assert data["detail"] == "Order not found"


def test_update_order():
    # Create an order
    create_response = client.post(
        "/orders",
        json={
            "product_id": 20,
            "quantity": 2,
            "price": 500
        }
    )

    assert create_response.status_code == 200

    order_id = create_response.json()["id"]

    # Update the order
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

    # 5 × 500 = 2500
    assert data["total_amount"] == 2500


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


def test_delete_order():
    # Create an order
    create_response = client.post(
        "/orders",
        json={
            "product_id": 30,
            "quantity": 1,
            "price": 100
        }
    )

    assert create_response.status_code == 200

    order_id = create_response.json()["id"]

    # Delete the order
    response = client.delete(f"/orders/{order_id}")

    assert response.status_code == 200

    data = response.json()

    assert data["message"] == "Order deleted successfully"

    # Verify that order no longer exists
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
            "quantity": 0,
            "price": 55000
        }
    )

    assert response.status_code == 422


def test_create_order_invalid_price():
    response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 3,
            "price": 0
        }
    )

    assert response.status_code == 422


def test_create_order_missing_field():
    response = client.post(
        "/orders",
        json={
            "product_id": 2,
            "quantity": 3
        }
    )

    assert response.status_code == 422