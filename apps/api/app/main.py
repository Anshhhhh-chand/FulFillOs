from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generator

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, case, create_engine, func, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'fulfillos.db'}")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class OrderStatus(str, Enum):
    created = "created"
    reserved = "reserved"
    confirmed = "confirmed"
    packing = "packing"
    dispatched = "dispatched"
    delivered = "delivered"
    failed = "failed"
    cancelled = "cancelled"


class ReservationStatus(str, Enum):
    active = "active"
    confirmed = "confirmed"
    released = "released"
    expired = "expired"


class RecommendationStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    completed = "completed"


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sku: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price_in_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), default="unit", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class StoreInventory(Base):
    __tablename__ = "store_inventory"
    __table_args__ = (UniqueConstraint("store_id", "product_id", name="uq_store_product"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    available_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(36))
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    customer_reference: Mapped[str | None] = mapped_column(String(120))
    store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"))
    status: Mapped[str] = mapped_column(String(30), default=OrderStatus.created.value, nullable=False)
    total_in_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delivery_postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    allocation_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fulfilled_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit_price_in_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    substitution_product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StockReservation(Base):
    __tablename__ = "stock_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=ReservationStatus.active.value, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DemandObservation(Base):
    __tablename__ = "demand_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    units_sold: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class DemandForecast(Base):
    __tablename__ = "demand_forecasts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    forecast_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predicted_units: Mapped[float] = mapped_column(Float, nullable=False)
    lower_bound: Mapped[float | None] = mapped_column(Float)
    upper_bound: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ReplenishmentRecommendation(Base):
    __tablename__ = "replenishment_recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"))
    destination_store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    recommended_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=RecommendationStatus.pending.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    city: str
    postal_code: str
    is_active: bool


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    sku: str
    name: str
    category: str
    description: str | None
    price_in_paise: int
    unit: str
    is_active: bool


class InventoryAdjustment(BaseModel):
    product_id: str
    quantity: int = Field(..., description="Positive inbound or negative adjustment")
    reason: str = Field(min_length=2, max_length=300)


class InventoryConsumption(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, le=100000)
    reason: str = Field(default="Recorded store consumption", min_length=2, max_length=300)


class OrderItemIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, le=1000)


class OrderIn(BaseModel):
    delivery_postal_code: str
    items: list[OrderItemIn] = Field(min_length=1)
    customer_reference: str | None = None
    idempotency_key: str | None = None


class RecommendationAction(BaseModel):
    status: RecommendationStatus


class CopilotMessage(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def serialize_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def ensure_risk_recommendation(db: Session, store_id: str, product_id: str, available_quantity: int) -> None:
    if available_quantity > 10:
        return
    suggested = max(1, 12 - available_quantity)
    reason = {"predictedDemand": 0, "availableQuantity": available_quantity, "incomingQuantity": 0, "safetyStock": 2, "recommendedQuantity": suggested, "reason": "Automatically created because available stock crossed the 10-unit risk threshold"}
    existing = db.scalar(select(ReplenishmentRecommendation).where(ReplenishmentRecommendation.destination_store_id == store_id, ReplenishmentRecommendation.product_id == product_id, ReplenishmentRecommendation.status == RecommendationStatus.pending.value))
    if existing:
        existing.recommended_quantity = max(existing.recommended_quantity, suggested)
        existing.reason_json = json.dumps(reason)
        existing.updated_at = utcnow()
    else:
        db.add(ReplenishmentRecommendation(destination_store_id=store_id, product_id=product_id, recommended_quantity=suggested, reason_json=json.dumps(reason), status=RecommendationStatus.pending.value))


def seed_database(db: Session) -> None:
    if db.scalar(select(func.count(Store.id))) > 0:
        return
    stores = [
        Store(id="store-12", name="Store 12 · Koramangala", city="Bengaluru", postal_code="560034", latitude=12.9352, longitude=77.6245),
        Store(id="store-07", name="Store 07 · Indiranagar", city="Bengaluru", postal_code="560038", latitude=12.9784, longitude=77.6408),
        Store(id="store-21", name="Store 21 · HSR Layout", city="Bengaluru", postal_code="560102", latitude=12.9116, longitude=77.6389),
    ]
    products = [
        Product(id="prod-milk", sku="MILK-1L", name="Full cream milk", category="Dairy", description="Fresh full cream dairy milk, 1 litre", price_in_paise=6800, unit="1 litre"),
        Product(id="prod-bread", sku="BREAD-WHT", name="Whole wheat bread", category="Bakery", description="Soft whole wheat sandwich bread", price_in_paise=4500, unit="400 g"),
        Product(id="prod-eggs", sku="EGGS-12", name="Farm eggs", category="Dairy", description="Free range brown eggs", price_in_paise=12000, unit="12 pieces"),
        Product(id="prod-bananas", sku="BANANA-6", name="Robusta bananas", category="Produce", description="Ripe robusta bananas", price_in_paise=5500, unit="6 pieces"),
        Product(id="prod-coffee", sku="COFFEE-200", name="South Indian filter coffee", category="Beverages", description="Aromatic ground coffee powder", price_in_paise=18000, unit="200 g"),
        Product(id="prod-oats", sku="OATS-500", name="Rolled oats", category="Breakfast", description="High fibre rolled oats", price_in_paise=15500, unit="500 g"),
    ]
    db.add_all(stores + products)
    db.flush()
    quantities = {
        "store-12": {"prod-milk": 12, "prod-bread": 36, "prod-eggs": 18, "prod-bananas": 20, "prod-coffee": 8, "prod-oats": 14},
        "store-07": {"prod-milk": 44, "prod-bread": 28, "prod-eggs": 40, "prod-bananas": 16, "prod-coffee": 20, "prod-oats": 24},
        "store-21": {"prod-milk": 30, "prod-bread": 22, "prod-eggs": 15, "prod-bananas": 12, "prod-coffee": 11, "prod-oats": 18},
    }
    for store_id, product_quantities in quantities.items():
        for product_id, qty in product_quantities.items():
            db.add(StoreInventory(store_id=store_id, product_id=product_id, available_quantity=qty, reserved_quantity=0))
            db.add(InventoryMovement(store_id=store_id, product_id=product_id, movement_type="inbound", quantity=qty, reason="Initial demo stock"))
    now = utcnow()
    for store_id, product_quantities in quantities.items():
        for product_id, qty in product_quantities.items():
            for day in range(1, 15):
                sold = max(1, int((qty / 5) + ((day * 3 + len(product_id)) % 7) - 2))
                start = now - timedelta(days=day)
                db.add(DemandObservation(store_id=store_id, product_id=product_id, window_start=start, window_end=start + timedelta(hours=4), units_sold=sold))
    policies = {
        "Reservation and stock adjustments": "Reservations reduce available quantity and increase reserved quantity atomically. Every stock change creates an inventory movement. Never manually edit current inventory without a movement reason.",
        "Order failure runbook": "An order fails when no active store can satisfy all requested quantities. Check the store inventory at reservation time and offer an in-stock substitution in the same category when price and size are compatible.",
        "Replenishment policy": "Target stock covers the next four hours of predicted demand plus 20 percent safety stock. Incoming stock counts toward net stock before recommending a transfer.",
    }
    for title, content in policies.items():
        db.add(KnowledgeDocument(title=title, source_type="policy", content=content, checksum=hashlib.sha256(content.encode()).hexdigest()))
    db.commit()


def order_response(db: Session, order: Order) -> dict[str, Any]:
    items = db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    store = db.get(Store, order.store_id) if order.store_id else None
    reservations = db.scalars(select(StockReservation).where(StockReservation.order_id == order.id)).all()
    return {
        "id": order.id,
        "orderNumber": order.order_number,
        "status": order.status,
        "deliveryPostalCode": order.delivery_postal_code,
        "customerReference": order.customer_reference,
        "totalInPaise": order.total_in_paise,
        "failureReason": order.failure_reason,
        "store": StoreOut.model_validate(store).model_dump() if store else None,
        "allocation": json.loads(order.allocation_json) if order.allocation_json else None,
        "createdAt": serialize_dt(order.created_at),
        "items": [
            {"id": i.id, "productId": i.product_id, "requestedQuantity": i.requested_quantity, "fulfilledQuantity": i.fulfilled_quantity, "unitPriceInPaise": i.unit_price_in_paise}
            for i in items
        ],
        "reservations": [{"id": r.id, "productId": r.product_id, "quantity": r.quantity, "status": r.status, "expiresAt": serialize_dt(r.expires_at)} for r in reservations],
    }


def candidate_stores(db: Session, postal_code: str, items: list[OrderItemIn]) -> list[dict[str, Any]]:
    stores = db.scalars(select(Store).where(Store.is_active.is_(True))).all()
    candidates = []
    for store in stores:
        inventory_by_product = {i.product_id: i for i in db.scalars(select(StoreInventory).where(StoreInventory.store_id == store.id)).all()}
        missing = []
        for item in items:
            inv = inventory_by_product.get(item.product_id)
            if not inv or inv.available_quantity < item.quantity:
                missing.append({"productId": item.product_id, "requested": item.quantity, "available": inv.available_quantity if inv else 0})
        if missing:
            continue
        postal_distance = abs(int(store.postal_code[-3:]) - int(postal_code[-3:])) if postal_code[-3:].isdigit() else 50
        pending = db.scalar(select(func.count(Order.id)).where(Order.store_id == store.id, Order.status.in_([OrderStatus.reserved.value, OrderStatus.confirmed.value, OrderStatus.packing.value]))) or 0
        distance_score = min(1.0, postal_distance / 100)
        queue_score = min(1.0, pending / 10)
        pick_score = min(1.0, len(items) / 10)
        score = round(0.4 * distance_score + 0.25 * queue_score + 0.2 * pick_score, 3)
        candidates.append({"store": store, "score": score, "reasons": {"distanceKm": round(max(0.5, postal_distance / 10), 1), "pendingOrders": pending, "estimatedPickMinutes": len(items) * 2 + 2, "missingItems": 0}})
    return sorted(candidates, key=lambda c: c["score"])


def make_forecasts(db: Session, store_id: str | None = None) -> int:
    now = utcnow()
    stores = db.scalars(select(Store).where(Store.is_active.is_(True), *([Store.id == store_id] if store_id else []))).all()
    products = db.scalars(select(Product).where(Product.is_active.is_(True))).all()
    created = 0
    for store in stores:
        for product in products:
            observations = db.scalars(select(DemandObservation).where(DemandObservation.store_id == store.id, DemandObservation.product_id == product.id).order_by(DemandObservation.window_start.desc()).limit(14)).all()
            avg = sum(o.units_sold for o in observations) / len(observations) if observations else 0
            predicted = round(max(0.0, avg * 1.15 + (2 if observations else 0)), 2)
            db.add(DemandForecast(store_id=store.id, product_id=product.id, forecast_window_start=now, forecast_window_end=now + timedelta(hours=4), predicted_units=predicted, lower_bound=round(predicted * 0.75, 2), upper_bound=round(predicted * 1.3, 2), model_version="weighted-baseline-v1"))
            inventory = db.scalar(select(StoreInventory).where(StoreInventory.store_id == store.id, StoreInventory.product_id == product.id))
            safety = math.ceil(predicted * 0.2)
            net = inventory.available_quantity if inventory else 0
            rec_qty = max(0, math.ceil(predicted + safety - net))
            if net <= 10 and rec_qty == 0:
                rec_qty = 1
            if rec_qty > 0:
                reason = {"predictedDemand": predicted, "availableQuantity": net, "incomingQuantity": 0, "safetyStock": safety, "recommendedQuantity": rec_qty, "reason": "Four-hour weighted demand baseline plus 20% safety stock"}
                existing = db.scalar(select(ReplenishmentRecommendation).where(ReplenishmentRecommendation.destination_store_id == store.id, ReplenishmentRecommendation.product_id == product.id, ReplenishmentRecommendation.status == RecommendationStatus.pending.value))
                if existing:
                    existing.recommended_quantity = rec_qty
                    existing.reason_json = json.dumps(reason)
                    existing.updated_at = now
                else:
                    db.add(ReplenishmentRecommendation(destination_store_id=store.id, product_id=product.id, recommended_quantity=rec_qty, reason_json=json.dumps(reason), status=RecommendationStatus.pending.value))
            created += 1
    db.commit()
    return created


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_database(db)
    yield


app = FastAPI(title="FulfillOS API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/v1/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"status": "ok", "database": "connected", "service": "fulfillos-api", "timestamp": utcnow().isoformat()}


@app.get("/api/v1/stores", response_model=list[StoreOut])
def list_stores(db: Session = Depends(get_db)) -> list[Store]:
    return db.scalars(select(Store).order_by(Store.name)).all()


@app.get("/api/v1/products", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db)) -> list[Product]:
    return db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name)).all()


@app.get("/api/v1/stores/{store_id}/inventory")
def get_inventory(store_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if not db.get(Store, store_id):
        raise HTTPException(404, "Store not found")
    rows = db.execute(select(StoreInventory, Product).join(Product, Product.id == StoreInventory.product_id).where(StoreInventory.store_id == store_id).order_by(Product.name)).all()
    return [{"id": inv.id, "storeId": store_id, "productId": product.id, "sku": product.sku, "productName": product.name, "category": product.category, "unit": product.unit, "availableQuantity": inv.available_quantity, "reservedQuantity": inv.reserved_quantity, "updatedAt": serialize_dt(inv.updated_at), "stockoutRisk": inv.available_quantity <= 10} for inv, product in rows]


@app.get("/api/v1/stores/{store_id}/inventory/movements")
def get_movements(store_id: str, limit: int = Query(default=50, le=200), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if not db.get(Store, store_id):
        raise HTTPException(404, "Store not found")
    rows = db.execute(select(InventoryMovement, Product).join(Product, Product.id == InventoryMovement.product_id).where(InventoryMovement.store_id == store_id).order_by(InventoryMovement.created_at.desc()).limit(limit)).all()
    return [{"id": m.id, "productId": p.id, "productName": p.name, "movementType": m.movement_type, "quantity": m.quantity, "reason": m.reason, "createdAt": serialize_dt(m.created_at)} for m, p in rows]


@app.post("/api/v1/stores/{store_id}/inventory/adjust")
def adjust_inventory(store_id: str, body: InventoryAdjustment, db: Session = Depends(get_db)) -> dict[str, Any]:
    inv = db.scalar(select(StoreInventory).where(StoreInventory.store_id == store_id, StoreInventory.product_id == body.product_id))
    if not inv:
        raise HTTPException(404, "Inventory item not found")
    if inv.available_quantity + body.quantity < 0:
        raise HTTPException(409, "Adjustment would make available stock negative")
    inv.available_quantity += body.quantity
    inv.version += 1
    db.add(InventoryMovement(store_id=store_id, product_id=body.product_id, movement_type="manual_adjustment", quantity=body.quantity, reason=body.reason))
    ensure_risk_recommendation(db, store_id, body.product_id, inv.available_quantity)
    db.commit()
    db.refresh(inv)
    return {"id": inv.id, "availableQuantity": inv.available_quantity, "reservedQuantity": inv.reserved_quantity, "movementCreated": True}


@app.post("/api/v1/stores/{store_id}/inventory/consume")
def consume_inventory(store_id: str, body: InventoryConsumption, db: Session = Depends(get_db)) -> dict[str, Any]:
    inv = db.scalar(select(StoreInventory).where(StoreInventory.store_id == store_id, StoreInventory.product_id == body.product_id))
    if not inv:
        raise HTTPException(404, "Inventory item not found")
    if inv.available_quantity < body.quantity:
        raise HTTPException(409, f"Only {inv.available_quantity} units are available")
    inv.available_quantity -= body.quantity
    inv.version += 1
    db.add(InventoryMovement(store_id=store_id, product_id=body.product_id, movement_type="sale", quantity=-body.quantity, reason=body.reason))
    if inv.available_quantity <= 10:
        ensure_risk_recommendation(db, store_id, body.product_id, inv.available_quantity)
        db.add(OutboxEvent(event_type="StockoutRiskDetected", aggregate_type="inventory", aggregate_id=inv.id, payload=json.dumps({"storeId": store_id, "productId": body.product_id, "availableQuantity": inv.available_quantity})))
    db.commit()
    db.refresh(inv)
    return {"id": inv.id, "availableQuantity": inv.available_quantity, "reservedQuantity": inv.reserved_quantity, "movementCreated": True, "movementType": "sale"}


@app.post("/api/v1/orders")
def create_order(body: OrderIn, db: Session = Depends(get_db), idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    idem = idempotency_key_header or body.idempotency_key
    if idem:
        existing = db.scalar(select(Order).where(Order.idempotency_key == idem))
        if existing:
            return order_response(db, existing)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([i.product_id for i in body.items]))).all()}
    if len(products) != len({i.product_id for i in body.items}):
        raise HTTPException(400, "One or more products do not exist")
    candidates = candidate_stores(db, body.delivery_postal_code, body.items)
    order = Order(order_number=f"ORD-{datetime.now().strftime('%y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}", customer_reference=body.customer_reference, delivery_postal_code=body.delivery_postal_code, idempotency_key=idem, status=OrderStatus.created.value)
    db.add(order)
    db.flush()
    total = 0
    if not candidates:
        order.status = OrderStatus.failed.value
        order.failure_reason = "No active store has enough available stock for every requested item."
        for item in body.items:
            db.add(OrderItem(order_id=order.id, product_id=item.product_id, requested_quantity=item.quantity, fulfilled_quantity=0, unit_price_in_paise=products[item.product_id].price_in_paise))
    else:
        allocation = candidates[0]
        order.store_id = allocation["store"].id
        order.status = OrderStatus.confirmed.value
        order.allocation_json = json.dumps({"storeId": allocation["store"].id, "score": allocation["score"], "reasons": allocation["reasons"]})
        for item in body.items:
            product = products[item.product_id]
            updated = db.execute(update(StoreInventory).where(StoreInventory.store_id == order.store_id, StoreInventory.product_id == item.product_id, StoreInventory.available_quantity >= item.quantity).values(available_quantity=StoreInventory.available_quantity - item.quantity, reserved_quantity=StoreInventory.reserved_quantity + item.quantity, version=StoreInventory.version + 1)).rowcount
            if updated != 1:
                db.rollback()
                raise HTTPException(409, "Stock changed while reserving the order; please retry")
            db.add(OrderItem(order_id=order.id, product_id=item.product_id, requested_quantity=item.quantity, fulfilled_quantity=item.quantity, unit_price_in_paise=product.price_in_paise))
            db.add(StockReservation(order_id=order.id, store_id=order.store_id, product_id=item.product_id, quantity=item.quantity, expires_at=utcnow() + timedelta(minutes=30)))
            db.add(InventoryMovement(store_id=order.store_id, product_id=item.product_id, movement_type="reservation", quantity=-item.quantity, reference_type="order", reference_id=order.id, reason="Order reservation"))
            total += product.price_in_paise * item.quantity
        db.add(OutboxEvent(event_type="OrderCreated", aggregate_type="order", aggregate_id=order.id, payload=json.dumps({"orderId": order.id, "orderNumber": order.order_number})))
    order.total_in_paise = total
    db.commit()
    return order_response(db, order)


@app.get("/api/v1/orders")
def list_orders(limit: int = Query(default=50, le=200), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    orders = db.scalars(select(Order).order_by(Order.created_at.desc()).limit(limit)).all()
    return [order_response(db, order) for order in orders]


@app.get("/api/v1/orders/{order_id}")
def get_order(order_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    order = db.get(Order, order_id) or db.scalar(select(Order).where(Order.order_number == order_id))
    if not order:
        raise HTTPException(404, "Order not found")
    return order_response(db, order)


@app.post("/api/v1/orders/{order_id}/cancel")
def cancel_order(order_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    if order.status in [OrderStatus.cancelled.value, OrderStatus.delivered.value, OrderStatus.failed.value]:
        return order_response(db, order)
    reservations = db.scalars(select(StockReservation).where(StockReservation.order_id == order.id, StockReservation.status == ReservationStatus.active.value)).all()
    for reservation in reservations:
        inv = db.scalar(select(StoreInventory).where(StoreInventory.store_id == reservation.store_id, StoreInventory.product_id == reservation.product_id))
        if inv:
            inv.available_quantity += reservation.quantity
            inv.reserved_quantity = max(0, inv.reserved_quantity - reservation.quantity)
            inv.version += 1
            db.add(InventoryMovement(store_id=reservation.store_id, product_id=reservation.product_id, movement_type="reservation_release", quantity=reservation.quantity, reference_type="order", reference_id=order.id, reason="Order cancelled"))
        reservation.status = ReservationStatus.released.value
    order.status = OrderStatus.cancelled.value
    db.commit()
    return order_response(db, order)


@app.post("/api/v1/forecasting/run")
def run_forecasting(store_id: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    count = make_forecasts(db, store_id)
    return {"createdForecasts": count, "modelVersion": "weighted-baseline-v1", "generatedAt": utcnow().isoformat()}


@app.get("/api/v1/stores/{store_id}/forecasts")
def list_forecasts(store_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if not db.get(Store, store_id):
        raise HTTPException(404, "Store not found")
    rows = db.execute(select(DemandForecast, Product).join(Product, Product.id == DemandForecast.product_id).where(DemandForecast.store_id == store_id).order_by(DemandForecast.predicted_units.desc(), Product.name)).all()
    return [{"id": f.id, "productId": p.id, "productName": p.name, "predictedUnits": f.predicted_units, "lowerBound": f.lower_bound, "upperBound": f.upper_bound, "modelVersion": f.model_version, "windowStart": serialize_dt(f.forecast_window_start), "windowEnd": serialize_dt(f.forecast_window_end)} for f, p in rows]


@app.get("/api/v1/stores/{store_id}/replenishment")
def list_replenishment(store_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if not db.get(Store, store_id):
        raise HTTPException(404, "Store not found")
    status_order = case((ReplenishmentRecommendation.status == RecommendationStatus.pending.value, 0), (ReplenishmentRecommendation.status == RecommendationStatus.approved.value, 1), (ReplenishmentRecommendation.status == RecommendationStatus.completed.value, 2), else_=3)
    rows = db.execute(select(ReplenishmentRecommendation, Product).join(Product, Product.id == ReplenishmentRecommendation.product_id).where(ReplenishmentRecommendation.destination_store_id == store_id).order_by(status_order, ReplenishmentRecommendation.recommended_quantity.desc(), ReplenishmentRecommendation.created_at.desc())).all()
    result = []
    for r, p in rows:
        reason = json.loads(r.reason_json)
        available = reason.get("availableQuantity", 0)
        result.append({"id": r.id, "productId": p.id, "productName": p.name, "destinationStoreId": store_id, "recommendedQuantity": r.recommended_quantity, "reason": reason, "priority": "critical" if available <= 3 else "urgent" if available <= 10 else "normal", "status": r.status, "createdAt": serialize_dt(r.created_at)})
    return result


@app.get("/api/v1/stores/{store_id}/alerts")
def list_store_alerts(store_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if not db.get(Store, store_id):
        raise HTTPException(404, "Store not found")
    rows = db.execute(select(StoreInventory, Product).join(Product, Product.id == StoreInventory.product_id).where(StoreInventory.store_id == store_id, StoreInventory.available_quantity <= 10).order_by(StoreInventory.available_quantity, Product.name)).all()
    alerts = []
    for inventory, product in rows:
        pending = db.scalar(select(ReplenishmentRecommendation).where(ReplenishmentRecommendation.destination_store_id == store_id, ReplenishmentRecommendation.product_id == product.id, ReplenishmentRecommendation.status == RecommendationStatus.pending.value))
        suggested = pending.recommended_quantity if pending else max(1, 12 - inventory.available_quantity)
        severity = "critical" if inventory.available_quantity <= 3 else "urgent"
        alerts.append({"id": f"stockout-{store_id}-{product.id}", "type": "stockout_risk", "severity": severity, "storeId": store_id, "productId": product.id, "productName": product.name, "availableQuantity": inventory.available_quantity, "recommendedQuantity": suggested, "message": f"{product.name} has {inventory.available_quantity} units available. Replenish {suggested} units now.", "action": "replenish", "createdAt": serialize_dt(inventory.updated_at)})
    return alerts


@app.post("/api/v1/replenishment/{recommendation_id}/{action}")
def update_replenishment(recommendation_id: str, action: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if action not in {"approve", "reject"}:
        raise HTTPException(400, "Action must be approve or reject")
    rec = db.get(ReplenishmentRecommendation, recommendation_id)
    if not rec:
        raise HTTPException(404, "Recommendation not found")
    if rec.status != RecommendationStatus.pending.value:
        return {"id": rec.id, "status": rec.status, "message": "Recommendation has already been processed"}
    if action == "approve":
        inventory = db.scalar(select(StoreInventory).where(StoreInventory.store_id == rec.destination_store_id, StoreInventory.product_id == rec.product_id))
        if not inventory:
            raise HTTPException(404, "Destination inventory item not found")
        inventory.available_quantity += rec.recommended_quantity
        inventory.version += 1
        db.add(InventoryMovement(store_id=rec.destination_store_id, product_id=rec.product_id, movement_type="transfer_in", quantity=rec.recommended_quantity, reference_type="replenishment", reference_id=rec.id, reason="Approved replenishment received"))
        db.add(OutboxEvent(event_type="ReplenishmentApproved", aggregate_type="replenishment", aggregate_id=rec.id, payload=json.dumps({"recommendationId": rec.id, "storeId": rec.destination_store_id, "productId": rec.product_id, "quantity": rec.recommended_quantity})))
        rec.status = RecommendationStatus.completed.value
        db.commit()
        return {"id": rec.id, "status": rec.status, "receivedQuantity": rec.recommended_quantity, "availableQuantity": inventory.available_quantity, "movementType": "transfer_in"}
    rec.status = RecommendationStatus.rejected.value
    db.commit()
    return {"id": rec.id, "status": rec.status}


@app.get("/api/v1/dashboard/overview")
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    today = utcnow() - timedelta(days=1)
    inventory = db.scalars(select(StoreInventory)).all()
    return {
        "ordersToday": db.scalar(select(func.count(Order.id)).where(Order.created_at >= today)) or 0,
        "failedOrders": db.scalar(select(func.count(Order.id)).where(Order.status == OrderStatus.failed.value)) or 0,
        "activeReservations": db.scalar(select(func.count(StockReservation.id)).where(StockReservation.status == ReservationStatus.active.value)) or 0,
        "productsAtRisk": sum(1 for i in inventory if i.available_quantity <= 10),
        "pendingReplenishment": db.scalar(select(func.count(ReplenishmentRecommendation.id)).where(ReplenishmentRecommendation.status == RecommendationStatus.pending.value)) or 0,
        "stores": db.scalar(select(func.count(Store.id)).where(Store.is_active.is_(True))) or 0,
    }


@app.post("/api/v1/copilot/sessions")
def create_session(db: Session = Depends(get_db)) -> dict[str, Any]:
    session = ChatSession()
    db.add(session)
    db.commit()
    return {"id": session.id, "createdAt": serialize_dt(session.created_at)}


def copilot_answer(db: Session, content: str) -> tuple[str, list[dict[str, Any]]]:
    lower = content.lower()
    citations: list[dict[str, Any]] = []
    if "order" in lower and ("fail" in lower or "why" in lower):
        token = next((part.strip("#,:.") for part in content.split() if part.upper().startswith("ORD-")), None)
        order = db.scalar(select(Order).where(Order.order_number == token)) if token else None
        if order and order.status == OrderStatus.failed.value:
            answer = f"{order.order_number} failed because no active store had enough available stock for every requested item. The recorded reason is: {order.failure_reason}"
            citations.append({"type": "order", "id": order.order_number, "label": "Order record"})
            return answer, citations
        if token:
            return f"I could not find a failed order with ID {token}. Check the order number and try again.", []
        return "Give me an order number such as ORD-260101120000-ABCD and I will inspect its recorded status and reservation evidence.", []
    if "stockout" in lower or "stock out" in lower or "at risk" in lower:
        rows = db.execute(select(StoreInventory, Product, Store).join(Product, Product.id == StoreInventory.product_id).join(Store, Store.id == StoreInventory.store_id).where(StoreInventory.available_quantity <= 10).order_by(StoreInventory.available_quantity)).all()
        if not rows:
            return "No products are currently at the configured stockout threshold of 10 units.", []
        summary = "; ".join(f"{p.name} at {s.name}: {i.available_quantity} available" for i, p, s in rows[:6])
        return f"I found {len(rows)} inventory positions at risk: {summary}.", [{"type": "inventory", "id": i.id, "label": f"{p.name} · {s.name}"} for i, p, s in rows[:6]]
    if "replenish" in lower or "restock" in lower:
        rows = db.execute(select(ReplenishmentRecommendation, Product, Store).join(Product, Product.id == ReplenishmentRecommendation.product_id).join(Store, Store.id == ReplenishmentRecommendation.destination_store_id).where(ReplenishmentRecommendation.status == RecommendationStatus.pending.value).order_by(ReplenishmentRecommendation.recommended_quantity.desc()).limit(5)).all()
        if not rows:
            return "There are no pending replenishment recommendations. Run the forecast job first.", []
        summary = "; ".join(f"{p.name} → {s.name}: {r.recommended_quantity} units" for r, p, s in rows)
        return f"Pending replenishment recommendations are {summary}.", [{"type": "recommendation", "id": r.id, "label": p.name} for r, p, s in rows]
    if "policy" in lower or "rule" in lower:
        documents = db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.is_active.is_(True))).all()
        terms = [term for term in lower.replace("?", " ").split() if len(term) > 3]
        doc = next((candidate for candidate in documents if any(term in candidate.title.lower() or term in candidate.content.lower() for term in terms)), documents[0] if documents else None)
        return (doc.content if doc else "No active policy document is indexed."), ([{"type": "runbook", "id": doc.id, "label": doc.title}] if doc else [])
    return "I can investigate failed orders, current stockout risk, and pending replenishment recommendations. Ask with a store or order number when you have one.", []


@app.post("/api/v1/copilot/sessions/{session_id}/messages")
def send_copilot_message(session_id: str, body: CopilotMessage, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not db.get(ChatSession, session_id):
        raise HTTPException(404, "Session not found")
    user_message = ChatMessage(session_id=session_id, role="user", content=body.content)
    db.add(user_message)
    answer, citations = copilot_answer(db, body.content)
    assistant_message = ChatMessage(session_id=session_id, role="assistant", content=answer, citations=json.dumps(citations))
    db.add(assistant_message)
    db.commit()
    return {"id": assistant_message.id, "role": "assistant", "content": answer, "citations": citations, "dataCheckedAt": utcnow().isoformat()}


@app.get("/api/v1/copilot/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not db.get(ChatSession, session_id):
        raise HTTPException(404, "Session not found")
    messages = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)).all()
    return {"id": session_id, "messages": [{"id": m.id, "role": m.role, "content": m.content, "citations": json.loads(m.citations) if m.citations else [], "createdAt": serialize_dt(m.created_at)} for m in messages]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
