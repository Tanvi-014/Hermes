# Hermes Middleware JavaScript SDK

The official JavaScript/TypeScript client SDK for the **Hermes Webhook Delivery Middleware**.

This SDK has **zero external dependencies** and is compatible with modern Node.js (18+), Browsers, Deno, Bun, and Cloudflare Workers.

## Installation

```bash
npm install hermes-middleware-sdk
```

## Quickstart

### Node.js (CommonJS)

```javascript
const { Hermes } = require('hermes-middleware-sdk');

// Initialize the client
const hermes = new Hermes('http://localhost:8000', {
  apiKey: 'hk_test_key_xyz'
});

// Ingest and deliver a webhook event with built-in retries
hermes.send('https://api.myapp.com/webhooks/receiver', {
  event: 'payment.succeeded',
  data: {
    id: 'pay_10293',
    amount: 2999,
    currency: 'usd'
  }
}, {
  idempotencyKey: 'idemp_pay_10293_success',
  headers: {
    'X-Custom-App-Header': 'production-delivery'
  }
}).then(response => {
  console.log(`Webhook Ingested! ID: ${response.webhook_id}`);
}).catch(err => {
  console.error(err);
});
```

## Advanced Features

### Event Filtering & Reshaping (Transformations)

Reshape webhook payloads or conditional filter them during ingestion:

```javascript
await hermes.send('https://api.myapp.com/webhooks/receiver', {
  event: 'payment.succeeded',
  amount: 2999
}, {
  filter: "event == 'payment.succeeded'",
  transform: {
    transaction_id: 'id',
    cents: 'amount'
  }
});
```

### Inspect & Replay Webhooks

```javascript
// Retrieve logs and attempts history
const details = await hermes.getWebhook('550e8400-e29b-41d4-a716-446655440000');
console.log(`Current status: ${details.status}`);

// Force immediate replay delivery
await hermes.replayWebhook('550e8400-e29b-41d4-a716-446655440000');
```
