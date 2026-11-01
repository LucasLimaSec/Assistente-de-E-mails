"""Envio pelo bot do Telegram (os comandos /feito, /nova etc. chegam na Fase 6)."""

import asyncio
import os

import telegram
import telegram.error
from telegram.constants import ParseMode

from src.report import dividir


class ErroTelegram(Exception):
    pass


def credenciais() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ErroTelegram("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID precisam estar no .env")
    return token, chat_id


def enviar(texto: str, bot_factory=telegram.Bot) -> int:
    """Envia o relatório (em várias mensagens se passar do limite) e devolve quantas foram enviadas."""
    token, chat_id = credenciais()
    mensagens = dividir(texto)

    async def mandar():
        async with bot_factory(token) as bot:
            for mensagem in mensagens:
                await bot.send_message(chat_id=chat_id, text=mensagem, parse_mode=ParseMode.HTML,
                                       link_preview_options=telegram.LinkPreviewOptions(is_disabled=True))

    try:
        asyncio.run(mandar())
    except telegram.error.InvalidToken as e:
        raise ErroTelegram("token recusado; confira TELEGRAM_BOT_TOKEN") from e
    except telegram.error.BadRequest as e:
        raise ErroTelegram(f"o Telegram recusou a mensagem ({e.message})") from e
    except telegram.error.Forbidden as e:
        raise ErroTelegram("o bot foi bloqueado no chat; mande /start para ele de novo") from e
    except telegram.error.TelegramError as e:
        raise ErroTelegram(f"falha ao enviar ({e.message})") from e
    return len(mensagens)
