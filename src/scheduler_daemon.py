import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from .config import (
    BOT_TOKEN,
    MEMORY_DIR,
    MONITORING_WATCHDOG_AUTOINSTALL,
    MONITORING_WATCHDOG_CHAT_ID,
    MONITORING_WATCHDOG_FAIL_THRESHOLD,
    MONITORING_WATCHDOG_HOST,
    MONITORING_WATCHDOG_INTERVAL_MINUTES,
    MONITORING_WATCHDOG_MODEL,
    MONITORING_WATCHDOG_PORTS,
    MONITORING_WATCHDOG_PROVIDER_CLI,
    MONITORING_WATCHDOG_RESUME_ARG,
    MONITORING_WATCHDOG_STATE_PATH,
    MONITORING_WATCHDOG_THREAD_ID,
    MONITORING_WATCHDOG_TIMEOUT_SECONDS,
    MONITORING_WATCHDOG_USER_ID,
    PROACTIVE_TOPIC_AUTOINSTALL,
    PROACTIVE_TOPIC_CHAT_ID,
    PROACTIVE_TOPIC_COOLDOWN_HOURS,
    PROACTIVE_TOPIC_INTERVAL_MINUTES,
    PROACTIVE_TOPIC_MAX_TOPICS,
    PROACTIVE_TOPIC_MODEL,
    PROACTIVE_TOPIC_PROVIDER_CLI,
    PROACTIVE_TOPIC_RESUME_ARG,
    PROACTIVE_TOPIC_SESSIONS_PATH,
    PROACTIVE_TOPIC_STATE_PATH,
    PROACTIVE_TOPIC_THREAD_ID,
    PROACTIVE_TOPIC_USER_ID,
    SCHEDULER_NOTIFY_CHAT_ID,
    SCHEDULER_NOTIFY_LEVEL,
    SCHEDULER_NOTIFY_THREAD_ID,
    TELEGRAM_REQUEST_TIMEOUT_SECONDS,
)
from .monitoring_watchdog_tool import ensure_monitoring_watchdog_schedule
from .providers import ProviderManager
from .scheduler import ScheduleManager
from .tasks import TaskManager
from .topic_proactive_tool import ensure_proactive_topic_schedule


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    bot = Bot(
        token=BOT_TOKEN,
        session=AiohttpSession(timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS),
    )
    task_manager = TaskManager(bot, provider_manager=ProviderManager())
    schedule_manager = ScheduleManager(
        task_manager,
        MEMORY_DIR / "schedules.db",
        notification_bot=bot,
        notification_chat_id=SCHEDULER_NOTIFY_CHAT_ID,
        notification_thread_id=SCHEDULER_NOTIFY_THREAD_ID,
        notify_level=SCHEDULER_NOTIFY_LEVEL,
    )
    task_manager.add_observer(schedule_manager)
    await task_manager.start()
    await schedule_manager.start()
    if MONITORING_WATCHDOG_AUTOINSTALL:
        ensured = await ensure_monitoring_watchdog_schedule(
            manager=schedule_manager,
            chat_id=MONITORING_WATCHDOG_CHAT_ID,
            user_id=MONITORING_WATCHDOG_USER_ID,
            message_thread_id=MONITORING_WATCHDOG_THREAD_ID,
            model=MONITORING_WATCHDOG_MODEL,
            provider_cli=MONITORING_WATCHDOG_PROVIDER_CLI,
            resume_arg=MONITORING_WATCHDOG_RESUME_ARG,
            interval_minutes=MONITORING_WATCHDOG_INTERVAL_MINUTES,
            host=MONITORING_WATCHDOG_HOST,
            ports=list(MONITORING_WATCHDOG_PORTS),
            timeout_seconds=MONITORING_WATCHDOG_TIMEOUT_SECONDS,
            fail_threshold=MONITORING_WATCHDOG_FAIL_THRESHOLD,
            state_path=MONITORING_WATCHDOG_STATE_PATH,
            python_bin=sys.executable,
        )
        if ensured is None:
            logging.warning(
                "Monitoring watchdog schedule auto-install skipped: missing chat_id/user_id "
                "(chat_id=%s user_id=%s)",
                MONITORING_WATCHDOG_CHAT_ID,
                MONITORING_WATCHDOG_USER_ID,
            )
        else:
            schedule_id, created = ensured
            logging.info(
                "Monitoring watchdog schedule %s: %s (chat=%s thread=%s host=%s ports=%s interval=%sm threshold=%s)",
                "created" if created else "already_present",
                schedule_id[:8],
                MONITORING_WATCHDOG_CHAT_ID,
                MONITORING_WATCHDOG_THREAD_ID,
                MONITORING_WATCHDOG_HOST,
                ",".join(str(port) for port in MONITORING_WATCHDOG_PORTS),
                MONITORING_WATCHDOG_INTERVAL_MINUTES,
                MONITORING_WATCHDOG_FAIL_THRESHOLD,
            )
    if PROACTIVE_TOPIC_AUTOINSTALL:
        ensured = await ensure_proactive_topic_schedule(
            manager=schedule_manager,
            chat_id=PROACTIVE_TOPIC_CHAT_ID,
            user_id=PROACTIVE_TOPIC_USER_ID,
            message_thread_id=PROACTIVE_TOPIC_THREAD_ID,
            model=PROACTIVE_TOPIC_MODEL,
            provider_cli=PROACTIVE_TOPIC_PROVIDER_CLI,
            resume_arg=PROACTIVE_TOPIC_RESUME_ARG,
            interval_minutes=PROACTIVE_TOPIC_INTERVAL_MINUTES,
            memory_dir=MEMORY_DIR,
            state_path=PROACTIVE_TOPIC_STATE_PATH,
            sessions_path=PROACTIVE_TOPIC_SESSIONS_PATH,
            max_topics=PROACTIVE_TOPIC_MAX_TOPICS,
            cooldown_hours=PROACTIVE_TOPIC_COOLDOWN_HOURS,
            python_bin=sys.executable,
        )
        if ensured is None:
            logging.warning(
                "Proactive topic schedule auto-install skipped: missing chat_id/user_id "
                "(chat_id=%s user_id=%s)",
                PROACTIVE_TOPIC_CHAT_ID,
                PROACTIVE_TOPIC_USER_ID,
            )
        else:
            schedule_id, created = ensured
            logging.info(
                "Proactive topic schedule %s: %s (chat=%s thread=%s interval=%sm max_topics=%s cooldown_hours=%s)",
                "created" if created else "already_present",
                schedule_id[:8],
                PROACTIVE_TOPIC_CHAT_ID,
                PROACTIVE_TOPIC_THREAD_ID,
                PROACTIVE_TOPIC_INTERVAL_MINUTES,
                PROACTIVE_TOPIC_MAX_TOPICS,
                PROACTIVE_TOPIC_COOLDOWN_HOURS,
            )
    logging.info(
        "Scheduler daemon started (notify_chat=%s notify_thread=%s notify_level=%s)",
        SCHEDULER_NOTIFY_CHAT_ID,
        SCHEDULER_NOTIFY_THREAD_ID,
        SCHEDULER_NOTIFY_LEVEL,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await schedule_manager.stop()
        await task_manager.stop()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
