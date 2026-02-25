import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from .config import BOT_TOKEN, METRICS_PORT
from .bot import router
from .metrics import start_metrics_server


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    start_metrics_server(METRICS_PORT)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Welcome message"),
        BotCommand(command="new", description="Start fresh conversation"),
        BotCommand(command="model", description="Switch model (sonnet/opus/haiku)"),
        BotCommand(command="provider", description="Switch or view LLM provider"),
        BotCommand(command="status", description="Show current session info"),
        BotCommand(command="memory", description="Show what I remember"),
        BotCommand(command="forget", description="Clear all memory"),
        BotCommand(command="tools", description="Show available tools"),
        BotCommand(command="cancel", description="Cancel current request"),
    ])

    logging.info("Bot starting...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
