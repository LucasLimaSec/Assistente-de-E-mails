"""Resumo semanal (sábado às 17:00): concluídas, pendentes, mais atrasadas e uso da IA na semana."""

import sqlite3
from datetime import datetime, time, timedelta
from html import escape

from src import kanban
from src.report import DIAS

# Preço do Haiku 5.5 por milhão de tokens (US$), para estimar o custo quando o provedor é o Claude.
PRECO_ENTRADA = 0.10
PRECO_SAIDA = 0.50
MAX_CONCLUIDAS = 10
MAX_ATRASADAS = 3


def inicio_semana(agora: datetime) -> datetime:
    """Segunda-feira 00:00 da semana de `agora`."""
    segunda = agora.date() - timedelta(days=agora.weekday())
    return datetime.combine(segunda, time(0), agora.tzinfo)


def montar(conn: sqlite3.Connection, cfg: dict, agora: datetime) -> str:
    desde = inicio_semana(agora)
    concluidas = kanban.concluidas_desde(conn, desde)
    colunas = kanban.quadro(conn, agora, cfg, agora)
    pendentes = colunas["a_fazer"] + colunas["em_andamento"] + colunas["aguardando"]
    atrasadas = sorted((t for t in colunas["a_fazer"] if t.atrasada), key=lambda t: -t.dias_parada)
    notas = kanban.notas_desde(conn, desde)

    partes = [f"📊 <b>Resumo da semana</b> ({desde:%d/%m} a {agora:%d/%m})\n"
              f"✅ {len(concluidas)} concluída(s) · 📋 {len(pendentes)} pendente(s) · "
              f"⚠️ {len(atrasadas)} atrasada(s)"]

    if concluidas:
        linhas = [f"• #{t.id} {escape(t.titulo)} ({DIAS[t.concluida_em.astimezone(agora.tzinfo).weekday()][:3].lower()})"
                  for t in concluidas]
        partes.append(_secao("✅ CONCLUÍDAS", linhas, MAX_CONCLUIDAS))
    if atrasadas:
        linhas = [f"• #{t.id} {escape(t.titulo)} (há {t.dias_parada} dias)" for t in atrasadas]
        partes.append(_secao("⚠️ MAIS ATRASADAS", linhas, MAX_ATRASADAS))
    if notas:
        partes.append(f"📝 {len(notas)} nota(s) registrada(s) na semana.")

    oportunidades = kanban.oportunidades(conn, desde)
    if oportunidades:
        titulo, aderencia = oportunidades[0]
        partes.append(f"💼 {len(oportunidades)} oportunidade(s) recebida(s); a melhor: "
                      f"{escape(titulo)} ({aderencia}/10).")

    partes.append(_uso_ia(conn, cfg, desde))
    return "\n\n".join(partes)


def _secao(titulo: str, linhas: list[str], maximo: int) -> str:
    corpo = linhas[:maximo] + ([f"+{len(linhas) - maximo}"] if len(linhas) > maximo else [])
    return f"<b>{titulo}</b>\n" + "\n".join(corpo)


def _milhar(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _uso_ia(conn: sqlite3.Connection, cfg: dict, desde: datetime) -> str:
    execucoes = [l for l in conn.execute(
        "SELECT iniciada_em, status, tokens_entrada, tokens_saida FROM execucoes WHERE tipo = 'diario'")
        if datetime.fromisoformat(l["iniciada_em"]) >= desde]
    entrada = sum(l["tokens_entrada"] for l in execucoes)
    saida = sum(l["tokens_saida"] for l in execucoes)
    falhas = sum(l["status"] == "falha" for l in execucoes)
    provedor = cfg["llm"]["provider"]
    if provedor == "claude":
        valor = entrada / 1e6 * PRECO_ENTRADA + saida / 1e6 * PRECO_SAIDA
        custo = f"custo estimado US$ {valor:.4f}".replace(".", ",")
    else:
        custo = "IA local, sem custo" if provedor == "ollama" else "sem IA, sem custo"
    texto = (f"🤖 <b>IA:</b> {len(execucoes)} execução(ões), {_milhar(entrada)} tokens de entrada / "
             f"{_milhar(saida)} de saída, {custo}.")
    if falhas:
        texto += f"\n⚠️ {falhas} execução(ões) com falha na semana."
    return texto
