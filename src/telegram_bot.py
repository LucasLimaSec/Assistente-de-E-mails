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


# --- bot permanente: comandos -------------------------------------------------------------------

def _agora(cfg: dict):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(cfg["geral"]["fuso_horario"]))


def _com_banco(cfg: dict, funcao, *args):
    """Abre o banco, roda o comando e fecha (cada comando numa conexão própria, numa thread)."""
    from src import storage
    from src.config import caminho_banco

    conn = storage.conectar(caminho_banco(cfg))
    try:
        return funcao(conn, *args)
    finally:
        conn.close()


async def _responder(update, texto: str) -> None:
    for parte in dividir(texto):
        await update.effective_message.reply_text(
            parte, parse_mode=ParseMode.HTML, link_preview_options=telegram.LinkPreviewOptions(is_disabled=True))


def montar_aplicacao(cfg: dict):
    """Application com os comandos, aceitando mensagens só do TELEGRAM_CHAT_ID."""
    from telegram import BotCommand
    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    from src import comandos
    from src.email_reader import buscar_email
    from src.llm import criar_provedor

    token, chat_id = credenciais()
    so_meu_chat = filters.Chat(chat_id=int(chat_id))

    async def ajuda(update, context):
        await _responder(update, comandos.AJUDA)

    def mover_handler(nome):
        async def handler(update, context):
            texto = await asyncio.to_thread(_com_banco, cfg, comandos.mover, nome, context.args, _agora(cfg))
            await _responder(update, texto)
        return handler

    async def nova(update, context):
        texto = await asyncio.to_thread(_com_banco, cfg, comandos.nova, " ".join(context.args), _agora(cfg))
        await _responder(update, texto)

    async def quadro(update, context):
        await _responder(update, await asyncio.to_thread(_com_banco, cfg, comandos.quadro, cfg, _agora(cfg)))

    async def semana(update, context):
        await _responder(update, await asyncio.to_thread(_com_banco, cfg, comandos.semana, cfg, _agora(cfg)))

    async def nota(update, context):
        texto = update.effective_message.text or ""
        await _responder(update, await asyncio.to_thread(_com_banco, cfg, comandos.nota, texto, _agora(cfg)))

    async def rascunho(update, context):
        n, pronta, gerar = await asyncio.to_thread(_com_banco, cfg, comandos.preparar_rascunho, context.args)
        if not gerar:
            await _responder(update, pronta)
            return
        await _responder(update, f"⏳ Gerando o rascunho de #{n} (no servidor leva 1 a 2 min)...")
        provedor = criar_provedor(cfg["llm"]["provider"], cfg)
        texto = await asyncio.to_thread(_com_banco, cfg, comandos.gerar_rascunho, cfg, n, _agora(cfg),
                                        buscar_email, provedor)
        await _responder(update, texto)

    async def desconhecido(update, context):
        await _responder(update, "Não conheço esse comando.\n\n" + comandos.AJUDA)

    async def menu(app):
        await app.bot.set_my_commands([
            BotCommand("quadro", "mostra o quadro"),
            BotCommand("feito", "conclui a tarefa N"),
            BotCommand("andamento", "move a tarefa N para Em Andamento"),
            BotCommand("aguardando", "move a tarefa N para Aguardando"),
            BotCommand("afazer", "devolve a tarefa N para A Fazer"),
            BotCommand("nova", "cria uma tarefa"),
            BotCommand("rascunho", "rascunho de resposta da tarefa N"),
            BotCommand("semana", "resumo da semana"),
            BotCommand("ajuda", "lista os comandos"),
        ])

    # concurrent_updates: um /rascunho demorado não trava os outros comandos.
    app = Application.builder().token(token).post_init(menu).concurrent_updates(True).build()
    for nome in ("start", "ajuda", "help"):
        app.add_handler(CommandHandler(nome, ajuda, filters=so_meu_chat))
    for nome in comandos.MOVER:
        app.add_handler(CommandHandler(nome, mover_handler(nome), filters=so_meu_chat))
    app.add_handler(CommandHandler("nova", nova, filters=so_meu_chat))
    app.add_handler(CommandHandler("quadro", quadro, filters=so_meu_chat))
    app.add_handler(CommandHandler("semana", semana, filters=so_meu_chat))
    app.add_handler(CommandHandler("rascunho", rascunho, filters=so_meu_chat))
    app.add_handler(MessageHandler(so_meu_chat & filters.COMMAND, desconhecido))
    # Texto livre vira nota. Mensagens de outros chats não casam com nenhum handler e são ignoradas.
    app.add_handler(MessageHandler(so_meu_chat & filters.TEXT & ~filters.COMMAND, nota))
    return app


def rodar_bot(cfg: dict) -> None:
    app = montar_aplicacao(cfg)
    app.run_polling(allowed_updates=["message"])
