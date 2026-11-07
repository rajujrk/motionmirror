"""Does the pinned broker really apply `consumer-timeout` from a policy to a quorum queue?

The long-attempt design depends on this, and AMQP heartbeats don't extend delivery deadlines. In RabbitMQ 4.3 a
timed-out 0-9-1 consumer is cancelled and its message goes back to the queue. The channel stays open, and the client
library drops the consumer silently, so the worker has to notice and subscribe again.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid

import aio_pika
import pytest

from motionmirror.worker.consumer import consume

IMAGE = "rabbitmq:4.3-management-alpine@sha256:eb338b3fc8f5c251028c0022ac0de3fe1db2d599d1c6a08b45898f756f248881"
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")


_containers: dict[str, str] = {}


def docker(*args: str, timeout: float = 60) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


@pytest.fixture(scope="module")
def broker() -> str:
    name = f"mm-deadline-{uuid.uuid4().hex[:8]}"
    docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "-p",
        "127.0.0.1::5672",
        # Check deadlines every second so the test doesn't have to wait a full minute.
        "-e",
        "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS=-rabbit channel_tick_interval 1000",
        IMAGE,
    )
    try:
        # Wait on the log. Running a rabbitmq CLI during startup races the server for the cookie file and crashes it.
        for _ in range(90):
            if "Server startup complete" in docker("logs", name):
                break
            subprocess.run(["sleep", "1"], check=True)
        port = docker("port", name, "5672/tcp").split(":")[-1]
        docker(
            "exec",
            name,
            "rabbitmqctl",
            "set_policy",
            "short",
            "^short",
            json.dumps({"consumer-timeout": 3000}),
            "--apply-to",
            "queues",
        )
        docker(
            "exec",
            name,
            "rabbitmqctl",
            "set_policy",
            "long",
            "^long",
            json.dumps({"consumer-timeout": 600000}),
            "--apply-to",
            "queues",
        )
        url = f"amqp://guest:guest@127.0.0.1:{port}/"
        _containers[url] = name
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


async def unacked(container_url: str, queue_name: str) -> int:
    """Off the event loop and bounded: this CLI call has been seen to hang indefinitely on a busy test broker."""
    command = (
        "exec", _containers[container_url], "rabbitmqctl", "-q", "--timeout", "15", "--formatter", "json",
        "list_queues", "name", "messages_unacknowledged",
    )  # fmt: skip
    for attempt in range(5):
        try:
            rows = json.loads(await asyncio.to_thread(docker, *command, timeout=20))
            return next(row["messages_unacknowledged"] for row in rows if row["name"] == queue_name)
        except (subprocess.SubprocessError, ValueError):
            if attempt == 4:
                raise
            await asyncio.sleep(2)
    raise AssertionError("unreachable")


async def hold_unacked(url: str, queue_name: str, seconds: float) -> int:
    """Take one message, hold it without acking, and report how many remain unacknowledged."""
    connection = await aio_pika.connect(url, timeout=20)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        queue = await channel.declare_queue(queue_name, durable=True, arguments={"x-queue-type": "quorum"})
        await channel.default_exchange.publish(aio_pika.Message(b"long task"), routing_key=queue_name)
        await channel.set_qos(prefetch_count=1)
        received = asyncio.Event()

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            received.set()

        await queue.consume(on_message, no_ack=False)
        await asyncio.wait_for(received.wait(), timeout=10)
        await asyncio.sleep(seconds)
        return await unacked(url, queue_name)


@pytest.mark.asyncio
async def test_a_short_consumer_timeout_takes_the_delivery_back(broker: str) -> None:
    assert await hold_unacked(broker, "short-1", seconds=10) == 0


@pytest.mark.asyncio
async def test_a_long_consumer_timeout_lets_the_delivery_outlive_the_short_one(broker: str) -> None:
    assert await hold_unacked(broker, "long-1", seconds=10) == 1


class _Settings:
    def __init__(self, url: str, queue_name: str) -> None:
        self.amqp_url = url
        self.queue_names = [queue_name]


@pytest.mark.asyncio
async def test_a_cancelled_consumer_subscribes_again_and_gets_the_redelivery(broker: str) -> None:
    queue_name = "short-2"
    connection = await aio_pika.connect(broker, timeout=20)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        await channel.declare_queue(queue_name, durable=True, arguments={"x-queue-type": "quorum"})
        await channel.default_exchange.publish(aio_pika.Message(b"task"), routing_key=queue_name)

    deliveries: list[bool] = []
    redelivered = asyncio.Event()

    class Handler:
        async def handle(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
            deliveries.append(message.redelivered)
            if len(deliveries) > 1:
                await message.ack()
                redelivered.set()
            # The first delivery is never settled, like an attempt running past its deadline.

    running = asyncio.create_task(consume(_Settings(broker, queue_name), Handler(), watchdog_seconds=1))
    try:
        await asyncio.wait_for(redelivered.wait(), timeout=30)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert deliveries == [False, True]
