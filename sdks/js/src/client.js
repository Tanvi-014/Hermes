/**
 * Official JavaScript SDK client for Hermes Webhook Delivery Middleware.
 * Compatible with Node.js (18+), Browsers, Deno, and Cloudflare Workers.
 */
class Hermes {
  /**
   * Initializes the Hermes client.
   * @param {string} baseUrl - The URL of the Hermes instance (e.g. "http://localhost:8000")
   * @param {object} [options] - Additional options
   * @param {string} [options.apiKey] - The API key for tenant verification
   */
  constructor(baseUrl, options = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.apiKey = options.apiKey || null;
  }

  /**
   * Ingests a webhook event through Hermes for high-reliability forwarding.
   * @param {string} destinationUrl - The downstream destination URL for this webhook
   * @param {object} payload - The JSON payload object
   * @param {object} [options] - Optional configurations
   * @param {object} [options.headers] - Custom headers to forward to the destination
   * @param {string} [options.idempotencyKey] - Unique key to guarantee exactly-once delivery
   * @param {string} [options.filter] - Conditional event filter expression
   * @param {object|string} [options.transform] - Custom mapping to reshape the payload
   * @param {string} [options.signatureProvider] - Optional provider (stripe, github, hermes)
   * @returns {Promise<object>} Response containing ingestion status and webhook ID
   */
  async send(destinationUrl, payload, options = {}) {
    const url = new URL(`${this.baseUrl}/api/v1/ingest`);

    if (destinationUrl) url.searchParams.set('url', destinationUrl);
    if (options.filter) url.searchParams.set('filter', options.filter);
    if (options.transform) {
      const transformVal = typeof options.transform === 'object'
        ? JSON.stringify(options.transform)
        : options.transform;
      url.searchParams.set('transform', transformVal);
    }
    if (options.signatureProvider) {
      url.searchParams.set('signature_provider', options.signatureProvider);
    }

    const headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };

    if (this.apiKey) {
      headers['X-Hermes-API-Key'] = this.apiKey;
    }
    if (options.idempotencyKey) {
      headers['Idempotency-Key'] = options.idempotencyKey;
    }

    // Pass custom headers through to destination
    if (options.headers) {
      for (const [key, value] of Object.entries(options.headers)) {
        headers[key] = value;
      }
    }

    try {
      const res = await fetch(url.toString(), {
        method: 'POST',
        headers,
        body: JSON.stringify(payload)
      });

      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.detail || `HTTP Error ${res.status}`);
      }
      return body;
    } catch (err) {
      throw new Error(`Hermes Ingestion Failed: ${err.message}`);
    }
  }

  /**
   * Retrieves detailed execution status and delivery attempts logs for a webhook.
   * @param {string} webhookId - The UUID of the webhook
   * @returns {Promise<object>}
   */
  async getWebhook(webhookId) {
    const url = `${this.baseUrl}/api/v1/webhooks/${webhookId}`;
    const headers = {};
    if (this.apiKey) {
      headers['X-Hermes-API-Key'] = this.apiKey;
    }

    try {
      const res = await fetch(url, { headers });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP Error ${res.status}`);
      return body;
    } catch (err) {
      throw new Error(`Failed to fetch webhook details: ${err.message}`);
    }
  }

  /**
   * Manually forces immediate replay of a failed/DLQ webhook.
   * @param {string} webhookId - The UUID of the webhook
   * @returns {Promise<object>}
   */
  async replayWebhook(webhookId) {
    const url = `${this.baseUrl}/api/v1/webhooks/${webhookId}/replay`;
    const headers = {};
    if (this.apiKey) {
      headers['X-Hermes-API-Key'] = this.apiKey;
    }

    try {
      const res = await fetch(url, { method: 'POST', headers });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP Error ${res.status}`);
      return body;
    } catch (err) {
      throw new Error(`Failed to replay webhook: ${err.message}`);
    }
  }
}

module.exports = { Hermes };
