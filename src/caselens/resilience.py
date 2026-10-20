"""One retry owner, per-provider concurrency/rate admission and a small circuit breaker."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from caselens.config import Limits
from caselens.errors import ClientError
from caselens.schemas import CallRecord, Usage, new_id
from caselens.tracing import CURRENT_RUN, record_call

T = TypeVar("T")
logger = logging.getLogger("caselens.api")


class AdmissionGate:
    def __init__(self, limits: Limits):
        self.limits = limits
        self._semaphore = asyncio.Semaphore(limits.concurrency)
        self._rate_lock = asyncio.Lock()
        self._next_request = 0.0
        self._failures = 0
        self._open_until = 0.0
        self._probe = False

    async def _admit(self):
        async with self._rate_lock:
            now = time.monotonic()
            if now < self._open_until or self._probe:
                raise ClientError("circuit_open", "provider temporarily paused", retryable=True)
            if self._open_until:
                self._probe = True
            if self.limits.requests_per_minute:
                await asyncio.sleep(max(0, self._next_request - now))
                self._next_request = time.monotonic() + 60 / self.limits.requests_per_minute


class Executor:
    def __init__(self, provider: str, model: str, limits: Limits, *, gate: AdmissionGate | None = None):
        self.provider, self.model, self.limits = provider, model, limits
        self.records: list[CallRecord] = []
        self.gate = gate or AdmissionGate(limits)

    async def run(self, operation: Callable[[], Awaitable[tuple[T, Usage]]]) -> T:
        started = time.monotonic()
        attempts, success, code = 0, False, None
        usage = Usage()
        request_id = new_id("req")
        try:
            async with self.gate._semaphore:
                for attempt in range(self.limits.max_attempts):
                    await self.gate._admit()
                    attempts += 1
                    try:
                        value, usage = await asyncio.wait_for(operation(), self.limits.timeout_seconds)
                        self.gate._failures, self.gate._open_until, self.gate._probe = 0, 0.0, False
                        success = True
                        return value
                    except TimeoutError:
                        error = ClientError("timeout", "model call exceeded timeout", retryable=True)
                    except ClientError as exc:
                        error = exc
                    self.gate._probe = False
                    if error.retryable:
                        self.gate._failures += 1
                        if self.gate._failures >= self.gate.limits.circuit_failures:
                            self.gate._open_until = time.monotonic() + self.gate.limits.circuit_cooldown_seconds
                    if not error.retryable or attempt + 1 == self.limits.max_attempts:
                        raise error
                    delay = error.retry_after
                    if delay is None:
                        delay = self.limits.backoff_seconds * 2**attempt
                        if delay:
                            delay += random.uniform(0, delay * .25)
                    await asyncio.sleep(min(30, max(0, delay)))
        except ClientError as exc:
            code = exc.code
            raise
        except asyncio.CancelledError:
            code = "cancelled"
            self.gate._probe = False
            raise
        finally:
            # Failed attempts may consume tokens; never report the last response as total usage.
            if attempts > 1:
                usage = Usage()
            record = CallRecord(run_id=CURRENT_RUN.get(), provider=self.provider, request_id=request_id, model=self.model,
                                attempts=attempts, latency_ms=(time.monotonic()-started)*1000,
                                success=success, usage=usage, error_code=code)
            record_call(record, self.records)
            logger.info("request=%s provider=%s model=%s attempts=%s success=%s latency_ms=%.1f error=%s",
                        request_id, self.provider, self.model, attempts, success, record.latency_ms, code)
