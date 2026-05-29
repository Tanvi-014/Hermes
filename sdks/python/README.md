# Hermes Middleware Python SDK

The official Python client SDK for the **Hermes Webhook Delivery Middleware**.

This SDK has **zero external dependencies** and is built on top of the Python standard library `urllib`.

## Installation

```bash
pip install hermes-middleware-sdk
```

## Quickstart

```python
from hermes.client import HermesClient

# Initialize the client pointing to your Hermes server
client = HermesClient("http://localhost:8000", api_key="hk_test_key_xyz")

# Ingest and deliver a webhook event with built-in retries
response = client.send(
    destination_url="https://api.myapp.com/webhooks/receiver",
    payload={
        "event": "payment.succeeded",
        "data": {
            "id": "pay_10293",
            "amount": 2999,
            "currency": "usd"
        }
    },
    headers={
        "X-Custom-App-Header": "production-delivery"
    },
    idempotency_key="idemp_pay_10293_success"
)

print(f"Webhook Ingested! ID: {response['webhook_id']}")
```

## Advanced Usage

### Event Filtering & Transformations

Configure conditional routing and structure mutation on ingestion:

```python
response = client.send(
    destination_url="https://api.myapp.com/webhooks/receiver",
    payload={"event": "payment.succeeded", "amount": 2999},
    filter_expression="event == 'payment.succeeded'",
    transform_expression={
        "transaction_id": "id",
        "cents": "amount"
    }
)
```

### Inspect and Replay Webhooks

```python
# Fetch detailed attempts logs
details = client.get_webhook("550e8400-e29b-41d4-a716-446655440000")
print(f"Current status: {details['status']}")

# Manually trigger immediate replay
client.replay_webhook("550e8400-e29b-41d4-a716-446655440000")
```
