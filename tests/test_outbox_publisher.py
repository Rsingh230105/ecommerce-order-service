from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import OutboxEvent
from app.outbox_publisher import (
    claim_pending_events,
    publish_pending_events,
)


class FakeSNSClient:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def publish(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return {"MessageId": "message-id"}


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def add_event(session_factory, event_id="event-1"):
    with session_factory() as db:
        event = OutboxEvent(
            event_id=event_id,
            event_type="ORDER_CREATED",
            aggregate_type="order",
            aggregate_id=7,
            payload={
                "event_id": event_id,
                "event_type": "ORDER_CREATED",
                "order_id": 7,
                "product_id": 3,
                "quantity": 2,
                "price": 12.5,
                "total_amount": 25.0,
                "status": "PENDING",
            },
        )
        db.add(event)
        db.commit()
        return event.id


def get_event(session_factory, event_id):
    with session_factory() as db:
        return db.scalar(
            select(OutboxEvent).where(OutboxEvent.event_id == event_id)
        )


def test_pending_event_is_published_and_marked_published(
    session_factory, monkeypatch
):
    add_event(session_factory)
    monkeypatch.setenv("SNS_ORDER_EVENTS_TOPIC_ARN", "topic-arn")
    sns_client = FakeSNSClient()

    processed = publish_pending_events(session_factory, sns_client=sns_client)
    event = get_event(session_factory, "event-1")

    assert processed == 1
    assert len(sns_client.calls) == 1
    assert sns_client.calls[0]["Message"] == (
        '{"event_id": "event-1", "event_type": "ORDER_CREATED", '
        '"order_id": 7, '
        '"product_id": 3, "quantity": 2, "price": 12.5, '
        '"total_amount": 25.0, "status": "PENDING"}'
    )
    assert event.status == "PUBLISHED"
    assert event.published_at is not None
    assert event.event_id == "event-1"


def test_sns_failure_keeps_event_and_records_retry_state(
    session_factory, monkeypatch
):
    add_event(session_factory)
    monkeypatch.setenv("SNS_ORDER_EVENTS_TOPIC_ARN", "topic-arn")
    sns_client = FakeSNSClient(error=RuntimeError("SNS unavailable"))

    processed = publish_pending_events(session_factory, sns_client=sns_client)
    event = get_event(session_factory, "event-1")

    assert processed == 1
    assert event.status == "PENDING"
    assert event.attempts == 1
    assert event.last_error == "SNS unavailable"
    assert event.published_at is None
    assert event.event_id == "event-1"


def test_missing_topic_arn_fails_before_claiming(session_factory, monkeypatch):
    add_event(session_factory)
    monkeypatch.delenv("SNS_ORDER_EVENTS_TOPIC_ARN", raising=False)
    monkeypatch.setattr(
        "app.outbox_publisher.SNS_ORDER_EVENTS_TOPIC_ARN",
        "",
    )

    with pytest.raises(RuntimeError, match="SNS_ORDER_EVENTS_TOPIC_ARN is required"):
        publish_pending_events(session_factory, sns_client=FakeSNSClient())

    event = get_event(session_factory, "event-1")
    assert event.status == "PENDING"
    assert event.attempts == 0
    assert event.claim_token is None


def test_claim_prevents_second_publisher_from_claiming_same_event(session_factory):
    add_event(session_factory)

    first_claim = claim_pending_events(session_factory)
    second_claim = claim_pending_events(session_factory)

    assert len(first_claim) == 1
    assert second_claim == []


def test_expired_claim_can_be_recovered(session_factory):
    event_id = add_event(session_factory)
    with session_factory() as db:
        event = db.get(OutboxEvent, event_id)
        event.status = "PUBLISHING"
        event.claim_token = "expired-claim"
        event.claimed_until = datetime.utcnow() - timedelta(seconds=1)
        db.commit()

    claims = claim_pending_events(session_factory)

    assert len(claims) == 1
    assert claims[0].event_id == "event-1"
    assert claims[0].claim_token != "expired-claim"
