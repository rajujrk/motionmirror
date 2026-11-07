"""Consumes tasks with manual acknowledgements. A message is acked only after its outcome is durable."""

from __future__ import annotations

import asyncio
import logging
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
                await message.ack()
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
            await message.ack()


async def consume(settings: Any, handler: TaskHandler) -> None:
    import aio_pika

    connection = await aio_pika.connect_robust(settings.amqp_url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        for name in settings.queue_names:
            # passive: this identity consumes topology, it doesn't create it.
            queue = await channel.declare_queue(name, passive=True)
            await queue.consume(handler.handle, no_ack=False)
            log.info("consuming %s", name)
        await asyncio.Future()
