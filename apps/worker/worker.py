import time

from sqlalchemy import select

from app.main import SessionLocal, StockReservation, StoreInventory, InventoryMovement, OutboxEvent, ReservationStatus, utcnow

MAX_ATTEMPTS = 5
KNOWN_EVENTS = {"OrderCreated", "InventoryReserved", "InventoryReservationReleased", "OrderFailed", "ProductUpdated", "ForecastGenerated", "ReplenishmentApproved", "StockoutRiskDetected"}


def process_once() -> int:
    processed = 0
    with SessionLocal() as db:
        expired = db.scalars(select(StockReservation).where(StockReservation.status == ReservationStatus.active.value, StockReservation.expires_at <= utcnow())).all()
        for reservation in expired:
            inv = db.scalar(select(StoreInventory).where(StoreInventory.store_id == reservation.store_id, StoreInventory.product_id == reservation.product_id))
            if inv:
                inv.available_quantity += reservation.quantity
                inv.reserved_quantity = max(0, inv.reserved_quantity - reservation.quantity)
                inv.version += 1
                db.add(InventoryMovement(store_id=reservation.store_id, product_id=reservation.product_id, movement_type="reservation_release", quantity=reservation.quantity, reference_type="order", reference_id=reservation.order_id, reason="Reservation expired"))
            reservation.status = ReservationStatus.expired.value
            processed += 1
        events = db.scalars(select(OutboxEvent).where(OutboxEvent.status == "pending", OutboxEvent.available_at <= utcnow(), OutboxEvent.attempts < MAX_ATTEMPTS).order_by(OutboxEvent.created_at).limit(20)).all()
        for event in events:
            event.status = "processing"
            event.attempts += 1
            try:
                if event.event_type not in KNOWN_EVENTS:
                    raise ValueError(f"Unsupported outbox event type: {event.event_type}")
                event.status = "processed"
                event.processed_at = utcnow()
                event.last_error = None
                processed += 1
            except Exception as exc:
                event.status = "failed" if event.attempts >= MAX_ATTEMPTS else "pending"
                event.available_at = utcnow()
                event.last_error = str(exc)
        db.commit()
    return processed


if __name__ == "__main__":
    while True:
        process_once()
        time.sleep(5)
