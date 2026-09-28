import os
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Order, OutboxEvent
from app.schemas import OrderCreate, OrderResponse, OrderUpdate

load_dotenv()

PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL") or "http://product-service:8000"

app = FastAPI(
    title="E-Commerce Order Service",
    version="1.0.0"
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "order-service"
    }


def get_product(product_id: int):
    url = f"{PRODUCT_SERVICE_URL}/products/{product_id}"

    try:
        response = httpx.get(url, timeout=5.0)
    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Product Service is unavailable"
        )

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail="Product not found"
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail="Product Service returned an error"
        )

    return response.json()


@app.post("/orders", response_model=OrderResponse)
def create_order(
    order: OrderCreate,
    db: Session = Depends(get_db)
):
    product = get_product(order.product_id)
    product_price = float(product["price"])
    total_amount = order.quantity * product_price

    new_order = Order(
        product_id=order.product_id,
        quantity=order.quantity,
        price=product_price,
        total_amount=total_amount,
        status="PENDING"
    )

    try:
        db.add(new_order)
        db.flush()

        event_id = str(uuid4())
        event_payload = {
            "event_id": event_id,
            "event_type": "ORDER_CREATED",
            "order_id": new_order.id,
            "product_id": new_order.product_id,
            "quantity": new_order.quantity,
            "price": new_order.price,
            "total_amount": new_order.total_amount,
            "status": new_order.status,
        }

        outbox_event = OutboxEvent(
            event_id=event_id,
            event_type="ORDER_CREATED",
            aggregate_type="order",
            aggregate_id=new_order.id,
            payload=event_payload,
            status="PENDING",
        )
        db.add(outbox_event)
        db.commit()
        db.refresh(new_order)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Order could not be created"
        ) from exc

    return new_order


@app.get("/orders", response_model=list[OrderResponse])
def get_orders(
    db: Session = Depends(get_db)
):
    return db.query(Order).all()


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    db: Session = Depends(get_db)
):
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .first()
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    return order


@app.put("/orders/{order_id}", response_model=OrderResponse)
def update_order(
    order_id: int,
    order_data: OrderUpdate,
    db: Session = Depends(get_db)
):
    existing_order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .first()
    )

    if not existing_order:
        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    existing_order.quantity = order_data.quantity
    existing_order.status = order_data.status
    existing_order.total_amount = (
        existing_order.quantity * existing_order.price
    )

    db.commit()
    db.refresh(existing_order)

    return existing_order


@app.delete("/orders/{order_id}")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db)
):
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .first()
    )

    if not order:
        raise HTTPException(
            status_code=404,
            detail="Order not found"
        )

    db.delete(order)
    db.commit()

    return {
        "message": "Order deleted successfully"
    }
