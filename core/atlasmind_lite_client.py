"""HTTP client for the atlasMind backend (design doc: AtlasMindLiteClient).

Adapter pattern: wraps the backend's HTTP API (docs/atlasmind_lite_api_contract.md) behind
typed models, translating httpx and in-band errors into LiteBackendError at this boundary.

Retry policy (consumer-side contract decision): transport errors and HTTP 503 are retried
with exponential backoff + jitter; HTTP 4xx and in-band "Error: ..." answers are not - the
backend already runs its own JQL retry loop.
"""

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from config.settings import LITE_ERROR_PREFIX, LiteSettings
from core.exceptions import LiteBackendError
from models.lite import (
    IssueDetailsRequest,
    IssueDetailsResponse,
    LiteQueryRequest,
    LiteQueryResult,
)

logger = structlog.get_logger(__name__)


class _ServiceUnavailableError(LiteBackendError):
    """HTTP 503 - backend model not initialised yet; retryable."""


class AtlasMindLiteClient:
    """Async client for GET /health and POST /query.

    The httpx.AsyncClient is injected (constructor injection, wired in server.py) and must
    be configured with the backend base_url and timeout from LiteSettings.
    """

    def __init__(self, http: httpx.AsyncClient, settings: LiteSettings) -> None:
        self._http = http
        self._settings = settings

    async def health(self) -> bool:
        """Pre-flight liveness check; False on any failure, never raises."""
        try:
            response = await self._http.get(self._settings.health_path)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def query(
        self,
        text: str,
        *,
        limit: int | None = None,
        jira_token: str | None = None,
        jira_email: str | None = None,
        jira_url: str | None = None,
    ) -> LiteQueryResult:
        """POST /query; returns the parsed result or raises LiteBackendError.

        The jira_* arguments become per-request auth headers for per-session credential
        binding; pass None to omit the corresponding header.
        """
        request = LiteQueryRequest(
            query=text,
            # TODO: use this id with POST /event to cancel aborted queries.
            request_id=str(uuid4()),
            limit=limit if limit is not None else self._settings.default_limit,
        )
        headers = {
            name: value
            for name, value in (
                ("X-Jira-Token", jira_token),
                ("X-Jira-Email", jira_email),
                ("X-Jira-Url", jira_url),
            )
            if value
        }
        try:
            response = await self._post_with_retry(
                self._settings.query_path, request.model_dump(exclude_none=True), headers
            )
        except (httpx.HTTPError, _ServiceUnavailableError) as exc:
            raise LiteBackendError(f"backend unreachable after retries: {exc}") from exc

        if response.status_code != 200:
            detail = self._error_detail(response)
            raise LiteBackendError(f"backend returned HTTP {response.status_code}: {detail}")

        try:
            result = LiteQueryResult.model_validate(response.json())
        except ValueError as exc:
            raise LiteBackendError(f"backend response does not match contract: {exc}") from exc

        # Contract: errors arrive as HTTP 200 with an "Error: " prefixed answer.
        if result.answer is not None and result.answer.startswith(LITE_ERROR_PREFIX):
            raise LiteBackendError(result.answer.removeprefix(LITE_ERROR_PREFIX))
        return result

    async def get_issue_details(
        self,
        issue_keys: list[str],
        *,
        comments_limit: int | None = None,
        request_id: str | None = None,
    ) -> IssueDetailsResponse:
        """POST /issue_details; returns raw per-issue content or raises LiteBackendError.

        See docs/atlasmind_lite_api_contract.md.
        Lists larger than 50 keys are split into batches of 50 and fanned out concurrently;
        results are merged. Keys not found in Jira are returned in `not_found`, not raised.
        """
        resolved_limit = (
            comments_limit if comments_limit is not None else self._settings.comments_limit_default
        )
        chunk_size = 50
        chunks = [issue_keys[i : i + chunk_size] for i in range(0, len(issue_keys), chunk_size)]

        if len(chunks) <= 1:
            return await self._fetch_issue_details_batch(
                chunks[0] if chunks else [], resolved_limit, request_id
            )

        batch_results = await asyncio.gather(
            *[self._fetch_issue_details_batch(chunk, resolved_limit, None) for chunk in chunks]
        )
        merged_issues = [issue for r in batch_results for issue in r.issues]
        merged_not_found = [key for r in batch_results for key in r.not_found]
        first_error = next((r.error for r in batch_results if r.error is not None), None)
        return IssueDetailsResponse(
            issues=merged_issues, not_found=merged_not_found, error=first_error
        )

    async def _fetch_issue_details_batch(
        self,
        issue_keys: list[str],
        comments_limit: int,
        request_id: str | None,
    ) -> IssueDetailsResponse:
        """POST /issue_details for a single batch of <= 50 keys."""
        request = IssueDetailsRequest(
            issue_keys=issue_keys,
            request_id=request_id or str(uuid4()),
            comments_limit=comments_limit,
        )
        try:
            response = await self._post_with_retry(
                self._settings.issue_details_path,
                request.model_dump(exclude_none=True),
                {},
            )
        except (httpx.HTTPError, _ServiceUnavailableError) as exc:
            raise LiteBackendError(f"backend unreachable after retries: {exc}") from exc

        if response.status_code != 200:
            detail = self._error_detail(response)
            raise LiteBackendError(f"backend returned HTTP {response.status_code}: {detail}")

        try:
            result = IssueDetailsResponse.model_validate(response.json())
        except ValueError as exc:
            raise LiteBackendError(f"backend response does not match contract: {exc}") from exc

        if result.error is not None and result.error.startswith(LITE_ERROR_PREFIX):
            raise LiteBackendError(result.error.removeprefix(LITE_ERROR_PREFIX))
        return result

    async def _post_with_retry(
        self, path: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._settings.max_retries),
            wait=wait_exponential_jitter(
                initial=self._settings.retry_initial_seconds,
                max=self._settings.retry_max_seconds,
            ),
            retry=retry_if_exception_type((httpx.TransportError, _ServiceUnavailableError)),
            reraise=True,
        )
        response: httpx.Response | None = None
        async for attempt in retrying:
            with attempt:
                response = await self._http.post(path, json=payload, headers=headers)
                if response.status_code == 503:
                    logger.warning("lite_backend_initialising", detail=self._error_detail(response))
                    raise _ServiceUnavailableError("backend model not initialised (HTTP 503)")
        assert response is not None  # reraise=True guarantees we only get here on success
        return response

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        return str(detail) if detail else response.text[:200]
