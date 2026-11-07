"""Consumes tasks with manual acknowledgements. A message is acked only after its outcome is durable."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from motionmirror.contracts.envelope import InvalidEnvelope, parse_envelope
from motionmirror.worker.api_client import ApiClient, ApiUnavailable, Disposition, LeaseLost
from motionmirror.worker.runner import AttemptRunner

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

    async def handle(self, message: DeliveredMessage) -> None:
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
                state = await self._runner.run(lease)
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


async def consume(settings: Any, handler: Any, watchdog_seconds: float = 10.0) -> None:
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
        while True:
            await asyncio.sleep(watchdog_seconds)
            await resubscribe_cancelled(channel, subscriptions, handler)
