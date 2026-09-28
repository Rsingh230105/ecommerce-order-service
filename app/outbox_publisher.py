import json
import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

import boto3
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import OutboxEvent


AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
SNS_ORDER_EVENTS_TOPIC_ARN = os.getenv("SNS_ORDER_EVENTS_TOPIC_ARN", "")
OUTBOX_BATCH_SIZE = int(os.getenv("OUTBOX_BATCH_SIZE", "10"))
OUTBOX_POLL_INTERVAL_SECONDS = float(
    os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "5")
)
OUTBOX_CLAIM_LEASE_SECONDS = int(
    os.getenv("OUTBOX_CLAIM_LEASE_SECONDS", "60")
)

REQUIRED_PAYLOAD_FIELDS = {
    "event_id",
    "event_type",
    "order_id",
    "product_id",
    "quantity",
}


@dataclass(frozen=True)
class ClaimedEvent:
    id: int
    event_id: str
    event_type: str
    payload: dict[str, Any]
    claim_token: str


def _topic_arn() -> str:
    topic_arn = os.getenv(
        "SNS_ORDER_EVENTS_TOPIC_ARN",
        SNS_ORDER_EVENTS_TOPIC_ARN,
    )
    if not topic_arn:
        raise RuntimeError("SNS_ORDER_EVENTS_TOPIC_ARN is required")
    return topic_arn


def claim_pending_events(
    session_factory: Callable[[], Session],
    batch_size: int = OUTBOX_BATCH_SIZE,
    lease_seconds: int = OUTBOX_CLAIM_LEASE_SECONDS,
) -> list[ClaimedEvent]:
    now = datetime.utcnow()
    claimed_until = now + timedelta(seconds=lease_seconds)

    with session_factory() as db:
        statement = (
            select(OutboxEvent)
            .where(
                or_(
                    OutboxEvent.status == "PENDING",
                    (
                        (OutboxEvent.status == "PUBLISHING")
                        & (OutboxEvent.claimed_until <= now)
                    ),
                )
            )
            .order_by(OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = list(db.scalars(statement))
        claims = []

        for event in events:
            claim_token = str(uuid4())
            event.status = "PUBLISHING"
            event.claim_token = claim_token
            event.claimed_until = claimed_until
            claims.append(
                ClaimedEvent(
                    id=event.id,
                    event_id=event.event_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    claim_token=claim_token,
                )
            )

        db.commit()
        return claims


def _validate_payload(payload: dict[str, Any]) -> None:
    missing_fields = REQUIRED_PAYLOAD_FIELDS - payload.keys()
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"Outbox payload missing required fields: {missing}")


def _mark_published(
    session_factory: Callable[[], Session],
    claim: ClaimedEvent,
) -> None:
    with session_factory() as db:
        event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == claim.id,
                OutboxEvent.claim_token == claim.claim_token,
                OutboxEvent.status == "PUBLISHING",
            )
        )
        if event is None:
            return

        event.status = "PUBLISHED"
        event.published_at = datetime.utcnow()
        event.claim_token = None
        event.claimed_until = None
        db.commit()


def _mark_failed(
    session_factory: Callable[[], Session],
    claim: ClaimedEvent,
    error: Exception,
) -> None:
    with session_factory() as db:
        event = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == claim.id,
                OutboxEvent.claim_token == claim.claim_token,
                OutboxEvent.status == "PUBLISHING",
            )
        )
        if event is None:
            return

        event.attempts += 1
        event.last_error = str(error)[:4000]
        event.status = "PENDING"
        event.claim_token = None
        event.claimed_until = None
        db.commit()


def publish_pending_events(
    session_factory: Callable[[], Session] = SessionLocal,
    sns_client: Any | None = None,
    batch_size: int = OUTBOX_BATCH_SIZE,
    lease_seconds: int = OUTBOX_CLAIM_LEASE_SECONDS,
) -> int:
    topic_arn = _topic_arn()
    client = sns_client or boto3.client("sns", region_name=AWS_REGION)
    claims = claim_pending_events(session_factory, batch_size, lease_seconds)

    for claim in claims:
        try:
            _validate_payload(claim.payload)
            client.publish(
                TopicArn=topic_arn,
                Subject=claim.event_type,
                Message=json.dumps(claim.payload),
            )
            _mark_published(session_factory, claim)
        except Exception as error:
            _mark_failed(session_factory, claim, error)

    return len(claims)


def run_publisher(stop_event: threading.Event | None = None) -> None:
    stopping = stop_event or threading.Event()

    while not stopping.is_set():
        publish_pending_events()
        stopping.wait(OUTBOX_POLL_INTERVAL_SECONDS)


def main() -> None:
    stop_event = threading.Event()

    def request_shutdown(signum: int, frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    run_publisher(stop_event)


if __name__ == "__main__":
    main()