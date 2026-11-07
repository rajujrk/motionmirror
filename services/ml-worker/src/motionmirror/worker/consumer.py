"""Consumes tasks with manual acknowledgements. A message is acked only after its outcome is durable."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from motionmirror.contracts.envelope import InvalidEnvelope, parse_envelope
from motionmirror.worker.api_client import ApiClient, ApiUnavailable, Disposition, LeaseLost
from motionmirror.worker.runner import AttemptRunner
from motionmirror.worker.telemetry import attempt_span

log = logging.getLogger(__name__)


class DeliveredMessage(Protocol):
    body: bytes

    async def ack(self) -> None: ...

    async def reject(self, requeue: bool = False) -> None: ...

    async def nack(self, requeue: bool = True) -> None: ...


async def settle(action: Callable[[], Awaitable[None]], job_id: str) -> None:
    """A late ack can fail after the broker already took the delivery back. The job's outcome is safe in PostgreSQL."""
    try:
        await action()
    except Exception as error:
        log.warning("could not settle the delivery for job %s: %s", job_id, type(error).__name__)


class TaskHandler:
    """Handles one delivery. The GPU semaphore is separate from prefetch, so redelivery can't double-book a device."""

    def __init__(self, api: ApiClient, runner: AttemptRunner, worker_id: str, max_generations: int = 1) -> None:
        self._api = api
        self._runner = runner
        self._worker_id = worker_id
        self._capacity = asyncio.Semaphore(max_generations)
        self._in_flight = 0
        self._idle = asyncio.Event()
        self._idle.set()

    async def handle(self, message: DeliveredMessage) -> None:
        self._in_flight += 1
        self._idle.clear()
        try:
            await self._handle(message)
        finally:
            self._in_flight -= 1
            if not self._in_flight:
                self._idle.set()

    async def drain(self, seconds: float) -> None:
        """Waits for running work. Past the limit it stops the attempt, which reports a failure and retries at once."""
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=seconds)
        except TimeoutError:
            log.warning("stopping the running attempt after waiting %.0f seconds", seconds)
            await self._runner.stop_active()
            await self._idle.wait()

    async def _handle(self, message: DeliveredMessage) -> None:
        try:
            envelope = parse_envelope(message.body)
        except InvalidEnvelope as invalid:
            # No requeue: this goes to the dead-letter queue instead of looping forever.
            log.error("rejected a malformed task: %s", invalid)
            await message.reject(requeue=False)
            return

        async with self._capacity:
            try:
                lease = await self._api.acquire(
                    envelope.job_id, envelope.event_id, envelope.dispatch_generation, self._worker_id
                )
            except ApiUnavailable:
                # The broker's delivery limit bounds this requeue, so it can't loop forever.
                log.warning("control plane unavailable while acquiring job %s", envelope.job_id)
                await asyncio.sleep(2)
                await message.nack(requeue=True)
                return
            except Exception:
                log.exception("acquire refused for job %s", envelope.job_id)
                await message.reject(requeue=False)
                return

            if isinstance(lease, Disposition):
                log.info("no work for job %s: %s", envelope.job_id, lease.kind)
                await settle(message.ack, envelope.job_id)
                return

            try:
                with attempt_span(envelope.trace, envelope.job_id, envelope.task_type, envelope.backend_id) as span:
                    state = await self._runner.run(lease)
                    span.set_attribute("mm.outcome", state)
                log.info("job %s recorded as %s", envelope.job_id, state)
            except LeaseLost:
                log.warning("lost the lease on job %s", envelope.job_id)
            except ApiUnavailable:
                # The outcome isn't recorded. Redelivery or the reconciler will recover it.
                log.warning("could not report job %s, leaving it for recovery", envelope.job_id)
                await message.nack(requeue=True)
                return
            except Exception:
                # The attempt's lease expires and Spring Boot's retry policy takes over.
                log.exception("attempt failed unexpectedly for job %s", envelope.job_id)
            await settle(message.ack, envelope.job_id)


async def resubscribe_cancelled(channel: Any, subscriptions: dict[str, Any], handler: Any) -> list[str]:
    """Consume again on any queue whose consumer the broker cancelled.

    When a delivery outlives its deadline, RabbitMQ 4.3 cancels the consumer and returns the message. The client
    library drops the consumer without telling anyone, so without this the worker would sit idle while looking healthy.
    """
    active = channel.channel.consumers
    restored = []
    for name, subscription in subscriptions.items():
        if subscription["tag"] not in active:
            log.error("the broker cancelled the consumer on %s, probably an attempt past its deadline", name)
            subscription["tag"] = await subscription["queue"].consume(handler.handle, no_ack=False)
            restored.append(name)
    return restored


async def consume(
    settings: Any,
    handler: Any,
    watchdog_seconds: float = 10.0,
    stop: asyncio.Event | None = None,
    drain_seconds: float = 60.0,
) -> None:
    """Runs until stop is set, then takes no new work and lets the current attempt finish, within a limit."""
    stop = stop or asyncio.Event()
    import aio_pika

    connection = await aio_pika.connect_robust(settings.amqp_url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        subscriptions: dict[str, Any] = {}
        for name in settings.queue_names:
            # passive: this identity consumes topology, it doesn't create it.
            queue = await channel.declare_queue(name, passive=True)
            subscriptions[name] = {"queue": queue, "tag": await queue.consume(handler.handle, no_ack=False)}
            log.info("consuming %s", name)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=watchdog_seconds)
            except TimeoutError:
                await resubscribe_cancelled(channel, subscriptions, handler)
        log.info("draining: taking no new work")
        for subscription in subscriptions.values():
            await subscription["queue"].cancel(subscription["tag"])
        await handler.drain(drain_seconds)
