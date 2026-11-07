"""Does the pinned broker really apply `consumer-timeout` from a policy to a quorum queue?

The long-attempt design depends on this, and AMQP heartbeats don't extend delivery deadlines. A short
deadline must close the channel, and a long one must let a delivery outlive the short one.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid

import aio_pika
import pytest

IMAGE = "rabbitmq:4.3-management-alpine@sha256:eb338b3fc8f5c251028c0022ac0de3fe1db2d599d1c6a08b45898f756f248881"
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


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
            "^short$",
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
            "^long$",
            json.dumps({"consumer-timeout": 600000}),
            "--apply-to",
            "queues",
        )
        yield f"amqp://guest:guest@127.0.0.1:{port}/"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


async def hold_unacked(url: str, queue_name: str, seconds: float) -> bool:
    """Take one message, hold it without acking, and report whether the channel survived."""
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
        return not channel.is_closed


@pytest.mark.asyncio
async def test_a_short_consumer_timeout_closes_the_channel(broker: str) -> None:
    assert not await hold_unacked(broker, "short", seconds=10)


@pytest.mark.asyncio
async def test_a_long_consumer_timeout_lets_the_delivery_outlive_the_short_one(broker: str) -> None:
    assert await hold_unacked(broker, "long", seconds=10)
