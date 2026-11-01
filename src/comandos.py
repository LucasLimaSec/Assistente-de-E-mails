"""Comandos do bot como funções puras: recebem o banco e os argumentos e devolvem o texto da resposta (HTML).

O telegram_bot.py só liga cada comando a um handler; assim tudo aqui é testável sem o Telegram.
"""

import imaplib
import sqlite3
from datetime import datetime
from html import escape

from src import kanban, report, resumo
from src.janela import inicio_janela

MAX_TITULO = 80

AJUDA = """<b>Comandos</b>
/quadro – mostra o quadro
/feito N – conclui a tarefa N (aceita vários: /feito 3 5)
/andamento N – move para Em Andamento
/aguardando N – move para Aguardando (volta sozinha quando chegar resposta)
/afazer N – devolve para A Fazer
/nova texto – cria uma tarefa (/nova ! texto = prioridade alta)
/rascunho N – rascunho de resposta da tarefa N (/rascunho N novo gera outro)
/semana – resumo da semana até agora

Qualquer outra mensagem de texto vira uma nota do dia e aparece em "Feito" no próximo relatório."""

MOVER = {"feito": "feito", "andamento": "em_andamento", "aguardando": "aguardando", "afazer": "a_fazer"}


def numeros(args: list[str]) -> list[int]:
    """'3', '#5', '7,8' -> [3, 5, 7, 8]."""
    lista = []
    for arg in args:
        for parte in arg.replace(",", " ").split():
            parte = parte.lstrip("#")
            if not parte.isdigit():
                raise ValueError(f"{escape(parte)!s} não é um número de tarefa")
            lista.append(int(parte))
    return lista


def mover(conn: sqlite3.Connection, comando: str, args: list[str], agora: datetime) -> str:
    coluna = MOVER[comando]
    try:
        alvos = numeros(args)
    except ValueError as e:
        return f"❌ {e}. Ex.: /{comando} 12"
    if not alvos:
        return f"Diga o número da tarefa. Ex.: /{comando} 12"
    respostas = []
    for n in alvos:
        tarefa = kanban.obter(conn, n)
        if tarefa is None:
            respostas.append(f"❌ #{n} não existe.")
            continue
        movida = kanban.mover(conn, n, coluna, agora, motivo="comando")
        destino = kanban.NOMES_COLUNAS[coluna]
        marca = "✅" if coluna == "feito" else "➡️"
        respostas.append(f"{marca} #{n} {escape(tarefa.titulo)} → {destino}" if movida
                         else f"#{n} já estava em {destino}.")
    return "\n".join(respostas)


def nova(conn: sqlite3.Connection, texto: str, agora: datetime) -> str:
    texto = " ".join(texto.split())
    prioridade = "media"
    if texto.startswith("!"):
        prioridade, texto = "alta", texto.lstrip("! ")
    if not texto:
        return "Escreva a tarefa depois do comando. Ex.: /nova Ligar para o contador"
    titulo = texto[:MAX_TITULO]
    numero = kanban.criar(conn, tipo="realizar", titulo=titulo, origem="telegram", agora=agora,
                          prioridade=prioridade)
    marca = report.MARCA_PRIORIDADE[prioridade]
    return f"{marca} Criada a tarefa #{numero}: {escape(titulo)}"


def quadro(conn: sqlite3.Connection, cfg: dict, agora: datetime) -> str:
    colunas = kanban.quadro(conn, agora, cfg, inicio_janela(agora, cfg))
    return report.montar_quadro(colunas, agora)


def nota(conn: sqlite3.Connection, texto: str, agora: datetime) -> str:
    if not texto.strip():
        return "Nota vazia."
    kanban.nova_nota(conn, texto, agora)
    return "📝 Anotado. Vai aparecer em \"Feito\" no próximo relatório."


def semana(conn: sqlite3.Connection, cfg: dict, agora: datetime) -> str:
    return resumo.montar(conn, cfg, agora)


# --- rascunho -----------------------------------------------------------------------------------

def preparar_rascunho(conn: sqlite3.Connection, args: list[str]) -> tuple[int | None, str | None, bool]:
    """Confere o pedido de /rascunho.

    Devolve (número da tarefa, resposta pronta, precisa gerar). Se a resposta vier pronta
    (erro ou rascunho já guardado), não é preciso chamar a IA.
    """
    novo = bool(args) and args[-1].lower() in ("novo", "outro", "refazer")
    try:
        alvos = numeros(args[:-1] if novo else args)
    except ValueError as e:
        return None, f"❌ {e}. Ex.: /rascunho 12", False
    if len(alvos) != 1:
        return None, "Diga o número de uma tarefa. Ex.: /rascunho 12", False
    n = alvos[0]
    tarefa = kanban.obter(conn, n)
    if tarefa is None:
        return n, f"❌ #{n} não existe.", False
    if kanban.email_da_tarefa(conn, n) is None:
        return n, f"#{n} não veio de um e-mail, então não há o que responder.", False
    if tarefa.rascunho and not novo:
        return n, formatar_rascunho(n, tarefa.titulo, tarefa.rascunho, guardado=True), False
    return n, None, True


def gerar_rascunho(conn: sqlite3.Connection, cfg: dict, n: int, agora: datetime, buscar, provedor) -> str:
    """Relê o e-mail da tarefa pelo IMAP, pede o rascunho à IA e guarda no banco."""
    from src.ai_analyzer import montar_contexto
    from src.llm.base import ErroIA

    tarefa = kanban.obter(conn, n)
    conta, message_id = kanban.email_da_tarefa(conn, n)
    try:
        email = buscar(cfg, conta, message_id)
    except (OSError, imaplib.IMAP4.error) as e:
        return f"❌ Não consegui reler o e-mail de #{n} ({escape(type(e).__name__)})."
    if email is None:
        return f"❌ Não achei mais o e-mail de #{n} na caixa de entrada."
    try:
        texto, entrada, saida = provedor.rascunhar(email, montar_contexto(cfg, agora.date()))
    except ErroIA as e:
        return f"❌ A IA não conseguiu gerar o rascunho ({escape(str(e))})."
    texto = texto.strip()
    kanban.salvar_rascunho(conn, n, texto)
    return formatar_rascunho(n, tarefa.titulo, texto)


def formatar_rascunho(n: int, titulo: str, texto: str, guardado: bool = False) -> str:
    rodape = "\n\n<i>Guardado de antes. Para gerar outro: /rascunho {n} novo</i>".format(n=n) if guardado else ""
    return f"✍️ <b>Rascunho para #{n}</b> ({escape(titulo)}):\n\n{escape(texto)}{rodape}"
