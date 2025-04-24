from __future__ import annotations
import asyncio
import logging
import sys
from typing import Any
import json
import pathlib

from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, BufferedInputFile

import httpx


def load_env_json(file_path=".env.json") -> Any:
    with open(file_path) as f:
        env_vars = json.load(f)
        return env_vars


ENV = load_env_json()

dp = Dispatcher()

import webpage2html

http_client = httpx.AsyncClient()


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(f"""Hello, {html.bold(message.from_user.full_name)}!
Send me a World Wide Web URL: https://example.com and I will send you a bundled HTML file!
""")


@dp.message()
async def link_handler(message: Message) -> None:
    link = message.html_text.strip()
    link = (
        link
        if link.startswith("http://") or link.startswith("https://")
        else "https://" + link
    )
    await message.reply(
        text=f"<b>Scheduled '{html.quote(link)}'</b>", parse_mode=ParseMode.HTML
    )

    agent = webpage2html.Agent(client=http_client)
    try:
        page = await agent.generate(
            link,
            username=None,
            password=None,
        )
        page = bytes(page, encoding="utf8")
    except Exception as e:
        await message.reply("Got exception: " + str(e))
        raise
    if page:
        file = BufferedInputFile(page, "index.html")
        await message.reply_document(file)
    if agent.errors:
        await message.reply(
            "While requesting your page I encountered some errors:\n\n"
            + str("\n".join(agent.errors))
        )

    # html = await http_client.get(message.html_text)
    # html = html.text
    # pathlib.Path(ENV["CACHE_PATH"]).joinpath(str(message.chat.id)).mkdir(exist_ok=True)

    #
    # async with http_client.stream("GET", message.html_text) as r:
    #     async for data in r.aiter_bytes():
    #          print(data)
    # html = await http_client.get(message.html_text)
    # html = html.content
    # await message.reply_document(BufferedInputFile(html, "response.html"))


pathlib.Path(ENV["CACHE_PATH"]).mkdir(exist_ok=True)

bot = Bot(
    token=ENV["BOT_TOKEN"], default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

logging.basicConfig(level=logging.INFO, stream=sys.stdout)


async def async_main() -> None:
    await dp.start_polling(bot)


asyncio.run(async_main())
