import asyncio
import logging
import signal
import traceback
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, settings
from app.db.session import async_session_factory, engine
from app.services.push import PushSender
from app.services.push_queue import DeliveryClaim, PushQueueService

logger = logging.getLogger(__name__)


def log_failure(message: str, error: BaseException) -> None:
    # Keep the stack for diagnosis without exception text or SQL parameters.
    logger.error(
        "%s (%s)\n%s",
        message,
        type(error).__name__,
        "".join(traceback.format_tb(error.__traceback__)),
    )


async def deliver(
    claim: DeliveryClaim,
    session_factory: async_sessionmaker[AsyncSession],
    sender: PushSender,
    config: Settings,
) -> None:
    async with session_factory() as session:
        prepared = await PushQueueService(session, config).prepare(claim)
    if prepared is None:
        return
    result = await sender.send(
        prepared.subscription, prepared.message, prepared.expires_at
    )
    async with session_factory() as session:
        await PushQueueService(session, config).record_result(prepared, result)


async def run_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    sender: PushSender,
    config: Settings,
) -> bool:
    async with session_factory() as session:
        enqueued = await PushQueueService(session, config).enqueue_due(
            datetime.now(UTC)
        )
    async with session_factory() as session:
        claims = await PushQueueService(session, config).claim_ready(datetime.now(UTC))
    outcomes = await asyncio.gather(
        *(deliver(claim, session_factory, sender, config) for claim in claims),
        return_exceptions=True,
    )
    failed = False
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            failed = True
            log_failure("Push delivery processing failed", outcome)
    return (enqueued > 0 or bool(claims)) and not failed


async def run_worker(
    session_factory: async_sessionmaker[AsyncSession],
    config: Settings,
    stop: asyncio.Event,
) -> None:
    sender = PushSender(config)
    while not stop.is_set():
        try:
            busy = await run_cycle(session_factory, sender, config)
        except Exception as error:
            log_failure("Push queue cycle failed", error)
            busy = False
        if not busy:
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.push_poll_seconds)
            except TimeoutError:
                pass


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run_worker(async_session_factory, settings, stop)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
