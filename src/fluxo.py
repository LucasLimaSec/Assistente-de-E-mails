"""Atualização diária do quadro: coleta, análise e gravação no banco (o envio ao Telegram vem na Fase 5)."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from src import kanban
from src.ai_analyzer import ResultadoAnalise, analisar, montar_contexto
from src.email_reader import Coleta, coletar


@dataclass
class ResultadoFluxo:
    coleta: Coleta
    analise: ResultadoAnalise | None
    resumo: kanban.Resumo
    ja_vistos: int = 0
    anexados: int = 0
    para_ia: int = 0
    erros: list[str] = field(default_factory=list)


def atualizar_quadro(cfg: dict, conn: sqlite3.Connection, inicio: datetime, agora: datetime,
                     prazo_limite: datetime | None = None, coletar_fn=coletar, analisar_fn=analisar) -> ResultadoFluxo:
    """Lê os e-mails da janela e aplica ao quadro. Rodar de novo não duplica nada nem reanalisa."""
    execucao = _iniciar_execucao(conn, agora)
    try:
        coleta = coletar_fn(cfg, inicio, agora)
        resumo = kanban.Resumo.vazio()

        novos, conhecidos, vistos = kanban.separar(conn, coleta.emails)
        for e, tarefa_id in conhecidos:
            kanban.anexar(conn, e, tarefa_id, agora, resumo)

        analise = None
        if novos:
            contexto = montar_contexto(cfg, agora.date(), kanban.para_ia(conn))
            analise = analisar_fn(novos, cfg, contexto, prazo_limite=prazo_limite)
            kanban.aplicar(conn, novos, analise.analises, agora, resumo)

        # Depois das tarefas novas: uma resposta já enviada hoje fecha a tarefa criada agora.
        kanban.registrar_enviados(conn, coleta.enviados, agora, resumo)

        resultado = ResultadoFluxo(coleta, analise, resumo, ja_vistos=vistos, anexados=len(conhecidos),
                                   para_ia=len(novos), erros=list(coleta.erros) + (analise.erros if analise else []))
        _terminar_execucao(conn, execucao, datetime.now(agora.tzinfo), "ok", analise,
                           "; ".join(resultado.erros) or None)
        return resultado
    except Exception as e:
        _terminar_execucao(conn, execucao, datetime.now(agora.tzinfo), "falha", None, f"{type(e).__name__}: {e}")
        raise


def _iniciar_execucao(conn: sqlite3.Connection, agora: datetime) -> int:
    with conn:
        cur = conn.execute("INSERT INTO execucoes (tipo, iniciada_em) VALUES ('diario', ?)", (agora.isoformat(),))
    return cur.lastrowid


def _terminar_execucao(conn: sqlite3.Connection, execucao: int, fim: datetime, status: str,
                       analise: ResultadoAnalise | None, erro: str | None) -> None:
    with conn:
        conn.execute(
            "UPDATE execucoes SET terminada_em = ?, status = ?, tokens_entrada = ?, tokens_saida = ?, erro = ? "
            "WHERE id = ?",
            (fim.isoformat(), status, analise.tokens_entrada if analise else 0,
             analise.tokens_saida if analise else 0, erro, execucao))
