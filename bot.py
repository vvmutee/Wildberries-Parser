import asyncio
import logging
import os
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from wb_parser import fetch_product

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ARTICLE_RE = re.compile(r"^\d{5,15}$")


def _stars(rating: float) -> str:
    full = int(rating)
    return "★" * full + "☆" * (5 - full)


def _format_price(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


async def start(update: Update, _) -> None:
    await update.message.reply_text(
        "Привет! Отправь мне артикул Wildberries, "
        "и я покажу информацию о товаре.\n\n"
        "Пример: <code>211486417</code>",
        parse_mode=ParseMode.HTML,
    )


async def handle_article(update: Update, _) -> None:
    text = update.message.text.strip()

    if not ARTICLE_RE.match(text):
        await update.message.reply_text(
            "Отправь артикул — число от 5 до 15 цифр."
        )
        return

    article = int(text)
    wait_msg = await update.message.reply_text("Ищу товар на Wildberries...")

    try:
        product = await fetch_product(article)
    except Exception:
        logger.exception("Ошибка при запросе к WB API (артикул %s)", article)
        await wait_msg.edit_text("Не удалось получить данные. Попробуй позже.")
        return

    if product is None:
        await wait_msg.edit_text("Товар не найден. Проверь артикул и попробуй снова.")
        return

    brand_line = f"  <b>Бренд:</b> {product.brand}\n" if product.brand else ""

    price_block = ""
    if product.sale_price_rub > 0:
        if product.price_rub > product.sale_price_rub:
            price_block = (
                f"  <b>Цена без скидки:</b> <s>{_format_price(product.price_rub)} ₽</s>\n"
                f"  <b>Цена:</b> {_format_price(product.sale_price_rub)} ₽\n"
            )
        else:
            price_block = f"  <b>Цена:</b> {_format_price(product.sale_price_rub)} ₽\n"
    else:
        price_block = "  <b>Цена:</b> нет данных\n"

    msg = (
        f"<b>{product.name}</b>\n\n"
        f"{brand_line}"
        f"  <b>Артикул:</b> <code>{product.article}</code>\n"
        f"{price_block}"
        f"  <b>Рейтинг:</b> {_stars(product.rating)} ({product.rating})\n"
        f"  <b>Отзывы:</b> {product.feedbacks}\n\n"
        f'<a href="{product.url}">Открыть на Wildberries</a>'
    )

    if product.photos:
        msg += "\n\n" + "\n".join(
            f'<a href="{url}">Фото {i}</a>' for i, url in enumerate(product.photos, 1)
        )

    await wait_msg.edit_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=False)


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Создай файл .env по примеру .env.example")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_article))

    logger.info("Бот запущен")
    async with app:
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await app.start()
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
