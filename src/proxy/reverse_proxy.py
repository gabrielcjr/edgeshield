"""Asynchronous Reverse Proxy Engine with Distributed Trace Propagation."""

import time
from typing import Set
import httpx
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from src.core.errors import UpstreamError
from src.telemetry.otel import tracer, upstream_latency_histogram

# Headers that must not be forwarded by a proxy (RFC 7230 §6.1)
HOP_BY_HOP_HEADERS: Set[str] = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class ReverseProxyEngine:
    """High-performance async reverse proxy forwarding requests to upstream microservices."""

    def __init__(self, upstream_base_url: str, timeout_seconds: float = 30.0):
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = httpx.AsyncClient(
            base_url=self.upstream_base_url,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=100, max_connections=200),
        )

    async def close(self) -> None:
        """Closes the underlying HTTP client session."""
        await self.client.aclose()

    async def proxy(self, request: Request, target_path: str = "") -> Response:
        """
        Forwards the incoming ASGI request to the upstream target service,
        injecting W3C TraceContext headers for SigNoz distributed tracing.
        """
        method = request.method
        url_path = target_path or request.url.path
        if request.url.query:
            url_path = f"{url_path}?{request.url.query}"

        # 1. Clean headers and preserve essential client info
        forward_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "host"
        }

        # 2. Inject W3C TraceContext for SigNoz distributed tracing
        carrier = {}
        TraceContextTextMapPropagator().inject(carrier)
        forward_headers.update(carrier)

        # 3. Read request body if present
        body = await request.body()

        start_time = time.monotonic()
        with tracer.start_as_current_span(f"edgeshield.proxy {method} {url_path}") as span:
            span.set_attribute("http.method", method)
            span.set_attribute("http.url", f"{self.upstream_base_url}{url_path}")

            try:
                upstream_req = self.client.build_request(
                    method=method,
                    url=url_path,
                    headers=forward_headers,
                    content=body if body else None,
                )
                upstream_res = await self.client.send(upstream_req, stream=True)

                duration = time.monotonic() - start_time
                upstream_latency_histogram.record(duration, {"method": method, "status": str(upstream_res.status_code)})
                span.set_attribute("http.status_code", upstream_res.status_code)

                # Filter upstream response headers
                res_headers = {
                    k: v for k, v in upstream_res.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
                }

                # Return streaming response
                return StreamingResponse(
                    upstream_res.aiter_raw(),
                    status_code=upstream_res.status_code,
                    headers=res_headers,
                    background=httpx.Response.aclose(upstream_res),
                )

            except httpx.TimeoutException as exc:
                span.record_exception(exc)
                raise UpstreamError(504, f"Upstream service timeout: {exc}")
            except httpx.RequestError as exc:
                span.record_exception(exc)
                raise UpstreamError(502, f"Failed to connect to upstream service: {exc}")
