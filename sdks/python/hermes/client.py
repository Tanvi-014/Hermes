import json
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

class HermesClient:
    """
    Official Python SDK client for Hermes Webhook Delivery Middleware.
    Designed with zero external dependencies using the Python standard library.
    """
    def __init__(self, base_url: str, api_key: Optional[str] = None, default_tenant_id: str = "anonymous"):
        """
        Initializes the Hermes client.
        
        :param base_url: The URL of the Hermes instance (e.g. "http://localhost:8000")
        :param api_key: The API key for tenant verification
        :param default_tenant_id: The default tenant mapping if API key is not strictly mapped
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.default_tenant_id = default_tenant_id

    def send(
        self,
        destination_url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        idempotency_key: Optional[str] = None,
        filter_expression: Optional[str] = None,
        transform_expression: Optional[str] = None,
        signature_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ingests a webhook event through Hermes for high-reliability forwarding.
        
        :param destination_url: The downstream destination URL for this webhook
        :param payload: The JSON payload dictionary
        :param headers: Optional additional headers to forward to the destination
        :param idempotency_key: Optional key to guarantee exactly-once delivery
        :param filter_expression: Optional event filter (e.g. "event.type == 'payment.succeeded'")
        :param transform_expression: Optional JSON mapping structure (as a JSON string or dict)
        :param signature_provider: Optional signature provider: stripe, github, or hermes
        :return: Dict containing execution status and webhook ID
        """
        url = f"{self.base_url}/api/v1/ingest"
        
        # Build query parameters
        params = []
        if destination_url:
            params.append(f"url={urllib.parse.quote(destination_url)}")
        if filter_expression:
            params.append(f"filter={urllib.parse.quote(filter_expression)}")
        if transform_expression:
            if isinstance(transform_expression, dict):
                transform_expression = json.dumps(transform_expression)
            params.append(f"transform={urllib.parse.quote(transform_expression)}")
        if signature_provider:
            params.append(f"signature_provider={urllib.parse.quote(signature_provider)}")
            
        if params:
            url = f"{url}?{'&'.join(params)}"

        # Prepare request headers
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            request_headers["X-Hermes-API-Key"] = self.api_key
        if idempotency_key:
            request_headers["Idempotency-Key"] = idempotency_key
            
        # Add custom headers (to be forwarded)
        if headers:
            for k, v in headers.items():
                request_headers[k] = v

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                detail = err_json.get("detail", err_body)
            except Exception:
                detail = err_body
            raise RuntimeError(f"Hermes Ingestion Failed (HTTP {e.code}): {detail}")
        except Exception as e:
            raise RuntimeError(f"Hermes Client Error: {e}")

    def get_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """
        Retrieves detailed execution status and delivery attempts logs for a webhook.
        
        :param webhook_id: UUID of the webhook
        """
        url = f"{self.base_url}/api/v1/webhooks/{webhook_id}"
        req = urllib.request.Request(url, method="GET")
        if self.api_key:
            req.add_header("X-Hermes-API-Key", self.api_key)
            
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Failed to fetch webhook details (HTTP {e.code}): {e.reason}")

    def replay_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """
        Manually forces replay of a webhook immediately.
        
        :param webhook_id: UUID of the webhook
        """
        url = f"{self.base_url}/api/v1/webhooks/{webhook_id}/replay"
        req = urllib.request.Request(url, method="POST")
        if self.api_key:
            req.add_header("X-Hermes-API-Key", self.api_key)
            
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Failed to replay webhook (HTTP {e.code}): {e.reason}")
