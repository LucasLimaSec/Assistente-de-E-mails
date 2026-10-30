"""Verificações do `main.py --check`: config, banco, chaves e conexões."""

import asyncio
import imaplib
import os
import urllib.request
from dataclasses import dataclass

import anthropic
import telegram
import telegram.error

from src import storage
from src.config import caminho_banco, validar_config

TIMEOUT = 20


@dataclass
class Resultado:
    nome: str
    ok: bool
    detalhe: str


def verificar_tudo(cfg: dict) -> list[Resultado]:
    erros = validar_config(cfg)
    if erros:
        # Sem config válido as demais verificações não fazem sentido.
        return [Resultado("Config", False, "; ".join(erros))]

    resultados = [Resultado("Config", True, "config.yaml válido"), verificar_banco(cfg)]
    if cfg["llm"]["provider"] == "claude":
        resultados.append(verificar_anthropic(cfg["llm"]["model"]))
    resultados.append(verificar_telegram())
    for conta in cfg["contas"]:
        resultados.append(verificar_conta(conta))
    if cfg.get("agenda", {}).get("enabled"):
        resultados.append(verificar_agenda())
    return resultados


def verificar_banco(cfg: dict) -> Resultado:
    caminho = caminho_banco(cfg)
    try:
        conn = storage.conectar(caminho)
        nomes = storage.tabelas(conn)
        conn.close()
    except Exception as e:
        return Resultado("Banco", False, f"{caminho}: {e}")
    return Resultado("Banco", True, f"{caminho} ({len(nomes)} tabelas: {', '.join(nomes)})")


def verificar_anthropic(modelo: str) -> Resultado:
    nome = "Claude API"
    chave = os.environ.get("ANTHROPIC_API_KEY")
    if not chave:
        return Resultado(nome, False, "ANTHROPIC_API_KEY não definida no .env")
    # models.retrieve não gera custo e confirma a chave e o modelo de uma vez.
    client = anthropic.Anthropic(api_key=chave, timeout=TIMEOUT, max_retries=1)
    try:
        info = client.models.retrieve(modelo)
    except anthropic.AuthenticationError:
        return Resultado(nome, False, "chave recusada (401); confira ANTHROPIC_API_KEY")
    except anthropic.PermissionDeniedError:
        return Resultado(nome, False, f"chave sem permissão para {modelo} (403)")
    except anthropic.NotFoundError:
        return Resultado(nome, False, f"modelo {modelo!r} não encontrado; confira llm.model")
    except anthropic.APIStatusError as e:
        return Resultado(nome, False, f"erro {e.status_code} da API")
    except anthropic.APIConnectionError:
        return Resultado(nome, False, "sem conexão com api.anthropic.com")
    return Resultado(nome, True, f"chave válida, modelo {info.id} disponível")


def verificar_telegram() -> Resultado:
    nome = "Telegram"
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return Resultado(nome, False, "TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID precisam estar no .env")

    async def consultar():
        async with telegram.Bot(token) as bot:
            eu = await bot.get_me()
            chat = await bot.get_chat(chat_id)
            return eu, chat

    try:
        eu, chat = asyncio.run(consultar())
    except telegram.error.InvalidToken:
        return Resultado(nome, False, "token recusado; confira TELEGRAM_BOT_TOKEN")
    except telegram.error.BadRequest as e:
        return Resultado(nome, False, f"chat {chat_id} inacessível ({e.message}); mande /start ao bot")
    except telegram.error.NetworkError as e:
        return Resultado(nome, False, f"sem conexão com o Telegram ({e.message})")
    quem = chat.first_name or chat.title or chat_id
    return Resultado(nome, True, f"bot @{eu.username} acessa o chat de {quem}")


def verificar_conta(conta: dict) -> Resultado:
    nome = f"E-mail [{conta['nome']}]"
    if conta["tipo"] != "imap":
        return Resultado(nome, False, f"tipo {conta['tipo']!r} ainda não implementado")
    endereco = os.environ.get(conta["endereco_env"])
    senha = os.environ.get(conta["senha_env"])
    faltando = [var for var, valor in ((conta["endereco_env"], endereco), (conta["senha_env"], senha)) if not valor]
    if faltando:
        return Resultado(nome, False, f"{' e '.join(faltando)} não definida(s) no .env")
    try:
        imap = imaplib.IMAP4_SSL(conta["servidor"], conta["porta"], timeout=TIMEOUT)
    except OSError as e:
        return Resultado(nome, False, f"sem conexão com {conta['servidor']}:{conta['porta']} ({e})")
    try:
        imap.login(endereco, senha)
        status, dados = imap.select("INBOX", readonly=True)
        if status != "OK":
            return Resultado(nome, False, "login ok, mas não abriu a caixa de entrada")
        total = int(dados[0])
    except imaplib.IMAP4.error as e:
        return Resultado(nome, False, f"login recusado ({e}); use uma senha de app")
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
    return Resultado(nome, True, f"{endereco} conectado, {total} mensagens na caixa de entrada")


def verificar_agenda() -> Resultado:
    nome = "Google Agenda"
    url = os.environ.get("GOOGLE_AGENDA_ICAL_URL")
    if not url:
        return Resultado(nome, False, "GOOGLE_AGENDA_ICAL_URL não definida no .env")
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resposta:
            inicio = resposta.read(64)
    except OSError as e:
        return Resultado(nome, False, f"não foi possível baixar a agenda ({e})")
    if not inicio.lstrip().startswith(b"BEGIN:VCALENDAR"):
        return Resultado(nome, False, "a URL não retornou um calendário iCal")
    return Resultado(nome, True, "endereço iCal acessível")
