# FulfillOS

Local operations intelligence platform for quick-commerce fulfilment.

## Run locally

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

In another terminal:

```bash
cd apps/web
npm install
npm run dev
```

Open <http://localhost:5173>. The API seeds three stores, six products, inventory, demand observations, and policy documents on first start. SQLite is the default so the demo runs without external services. Set `DATABASE_URL` to a PostgreSQL URL for the Docker Compose path.

### Exercise the flows from the dashboard

- Open **Orders → Create order**. Enter a postal code, product, and quantity. The form calls `POST /api/v1/orders`, allocates an eligible store, reserves stock, and refreshes the order list. Use a quantity larger than the visible stock to create a recorded failed order.
- Open **Inventory → Consume** beside any product. Enter units consumed to create a `sale` movement and reduce available stock. Repeat it until the item is at stockout risk, then run **Forecast & replenish → Run forecast**.
- Use **Inventory → Adjust** for cycle-count corrections. Adjustments are kept separate from sales in the movement ledger.
- In **Forecast & replenish**, **Receive stock** is a real receipt: it increases destination available stock, writes a `transfer_in` movement, marks the recommendation `completed`, and emits a `ReplenishmentApproved` outbox event. **Reject** only closes the recommendation.
- When a store reaches 10 units or fewer, FulfillOS raises a live urgent/critical banner, records a `StockoutRiskDetected` outbox event after consumption, and keeps pending replenishment items at the top of the queue. Re-running a forecast updates the existing pending recommendation instead of creating duplicates.

The same flows are available over HTTP:

```bash
curl -X POST http://localhost:8000/api/v1/orders \
  -H 'Content-Type: application/json' \
  -d '{"delivery_postal_code":"560034","items":[{"product_id":"prod-milk","quantity":2}],"idempotency_key":"demo-1"}'

curl -X POST http://localhost:8000/api/v1/stores/store-12/inventory/consume \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"prod-milk","quantity":5,"reason":"Demo sale stream"}'
```

Live stock alerts are available at `GET /api/v1/stores/{store_id}/alerts`. Consuming or manually lowering a product below 10 units automatically creates or updates one pending urgent replenishment recommendation and emits a stock-risk outbox event.

## Verify

```bash
cd apps/api
pytest -q
```

The dashboard covers overview, inventory movements and adjustments, orders, forecasting/replenishment approval, and a grounded read-only operations copilot. The worker can be started with `PYTHONPATH=apps/api python -m apps.worker.worker` to release expired reservations and process outbox rows.

See [`docs/demo.md`](docs/demo.md) for a repeatable walkthrough and [`docs/architecture.md`](docs/architecture.md) for the system design and consistency boundaries.

## Resume framing

**FulfillOS — Quick-commerce fulfilment and inventory intelligence platform**

- Built transaction-safe order allocation and inventory reservation with idempotency keys, signed movement ledger entries, cancellation release, and stock-risk alerts.
- Implemented explainable demand forecasting, de-duplicated replenishment recommendations, and receipt workflows that update inventory and emit outbox events.
- Delivered a React operations dashboard for store inventory, order failures, replenishment decisions, and grounded read-only copilot investigations.

Only add latency, throughput, or accuracy numbers after measuring them with a repeatable benchmark.

## Docker services

`docker compose up --build` starts PostgreSQL/pgvector, Redis, Ollama, API, worker, and web. The core demo does not require Ollama; the copilot uses deterministic database tools locally and remains available when the model service is absent.
