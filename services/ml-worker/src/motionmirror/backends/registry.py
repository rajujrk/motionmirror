"""Backends are chosen by trusted deployment settings. Heavy modules load only when actually selected."""

from __future__ import annotations

from collections.abc import Callable

from motionmirror.backends.base import VideoReplacementBackend


def _mock() -> VideoReplacementBackend:
    from motionmirror.backends.mock import MockReplacementBackend

    return MockReplacementBackend()


def _wan_animate() -> VideoReplacementBackend:
    from motionmirror.backends.wan_animate import WanAnimateReplacementBackend

    return WanAnimateReplacementBackend.from_environment()


_FACTORIES: dict[str, Callable[[], VideoReplacementBackend]] = {
    "mock": _mock,
    "wan-animate": _wan_animate,
}


class UnknownBackendError(ValueError):
    """This worker doesn't have that backend."""


def create_backend(backend_id: str) -> VideoReplacementBackend:
    factory = _FACTORIES.get(backend_id)
    if factory is None:
        raise UnknownBackendError(f"unknown backend {backend_id!r}")
    return factory()
