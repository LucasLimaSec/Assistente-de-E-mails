"""Rotinas do dia: análise de madrugada, relatório diário e resumo semanal.

Usadas tanto pela linha de comando (--relatorio, --resumo-semanal) quanto pelo agendador (--servico).
Os módulos fluxo e telegram_bot são acessados pelo nome do módulo para os testes poderem trocá-los.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src import agenda, fluxo, kanban, report, resumo, storage, telegram_bot
from src.config import DIAS_SEMANA, caminho_banco
from src.janela import inicio_janela

log = logging.getLogger("assistente.rotinas")
MAX_RODADAS_ANALISE = 5   # rodadas extras para e-mails que chegam durante a análise


@dataclass
class Relatorio:
    texto: str
    mensagens: int = 0                  # 0 no dry-run
    resultado: fluxo.ResultadoFluxo | None = None


@dataclass
class ResultadoMadrugada:
    rodadas: int = 0
    analisados: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    erros: list[str] = field(default_factory=list)


def hora_config(texto: str) -> time:
    h, m = map(int, texto.split(":"))
    return time(h, m)


def prazo_da_analise(cfg: dict, agora: datetime) -> datetime:
    """5 minutos antes do relatório de hoje: o que não ficar pronto até lá vai pelas regras."""
    fuso = ZoneInfo(cfg["geral"]["fuso_horario"])
    relatorio = datetime.combine(agora.astimezone(fuso).date(), hora_config(cfg["geral"]["horario_relatorio"]), fuso)
    return relatorio - timedelta(minutes=5)


def dia_util(cfg: dict, agora: datetime) -> bool:
    return DIAS_SEMANA[agora.weekday()] in cfg["geral"]["dias_uteis"]


def analise_madrugada(cfg: dict, relogio, prazo_limite: datetime) -> ResultadoMadrugada:
    """Analisa os e-mails da janela; repete enquanto chegarem e-mails novos e houver tempo."""
    total = ResultadoMadrugada()
    conn = storage.conectar(caminho_banco(cfg))
    try:
        while total.rodadas < MAX_RODADAS_ANALISE:
            agora = relogio()
            if agora >= prazo_limite:
                break
            r = fluxo.atualizar_quadro(cfg, conn, inicio_janela(agora, cfg), agora, prazo_limite=prazo_limite)
            total.rodadas += 1
            total.analisados += r.para_ia
            total.erros += r.erros
            if r.analise:
                total.tokens_entrada += r.analise.tokens_entrada
                total.tokens_saida += r.analise.tokens_saida
            if r.para_ia == 0:
                break
    finally:
        conn.close()
    log.info("análise da madrugada: %d rodada(s), %d e-mail(s) analisado(s), tokens %d/%d",
             total.rodadas, total.analisados, total.tokens_entrada, total.tokens_saida)
    return total


def relatorio_diario(cfg: dict, agora: datetime, *, horas: int | None = None, dry_run: bool = False,
                     prazo_limite: datetime | None = None, avisos_extra: list[str] | None = None) -> Relatorio:
    """Coleta o que falta, atualiza o quadro, monta o relatório e envia (ErroTelegram sobe para quem chamou).

    Com dry_run tudo roda numa cópia do banco em memória: nada é gravado nem enviado.
    """
    ultimo_relatorio = inicio_janela(agora, cfg)
    inicio = agora - timedelta(hours=horas) if horas else ultimo_relatorio
    banco = storage.conectar(caminho_banco(cfg))
    conn = banco
    if dry_run:
        conn = sqlite3.connect(":memory:")
        banco.backup(conn)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        r = fluxo.atualizar_quadro(cfg, conn, inicio, agora, prazo_limite=prazo_limite)
        colunas = kanban.quadro(conn, agora, cfg, ultimo_relatorio)
        avisos = list(avisos_extra or []) + avisos_do_fluxo(r, cfg)
        agenda_feita, agenda_hoje = [], []
        if agenda.ativa(cfg):
            try:
                agenda_feita, agenda_hoje = agenda.feitos_e_hoje(cfg, ultimo_relatorio, agora)
            except agenda.ErroAgenda as e:
                avisos.append(f"Google Agenda: {e}")
        dados = report.DadosRelatorio(
            agora=agora, desde=ultimo_relatorio, colunas=colunas,
            oportunidades=[report.Oportunidade(t, a) for t, a in kanban.oportunidades(conn, inicio)],
            avisos=avisos, marcar_conta=len(cfg["contas"]) > 1,
            notas=kanban.notas_desde(conn, ultimo_relatorio), agenda_feita=agenda_feita, agenda_hoje=agenda_hoje)
        texto = report.montar(dados, cfg["kanban"]["max_itens_por_coluna"],
                              (cfg.get("oportunidades") or {}).get("max_no_relatorio", 5))
        if dry_run:
            return Relatorio(texto, 0, r)
        mensagens = telegram_bot.enviar(texto)
        # O retrato do dia também marca "relatório enviado hoje" (usado na recuperação após reinício).
        kanban.salvar_snapshot(conn, agora.date(), colunas)
        log.info("relatório enviado (%d mensagem(ns))", mensagens)
        return Relatorio(texto, mensagens, r)
    finally:
        if conn is not banco:
            conn.close()
        banco.close()


def avisos_do_fluxo(r: fluxo.ResultadoFluxo, cfg: dict) -> list[str]:
    """Avisos curtos para o fim do relatório: o bot não fica em silêncio quando algo falha."""
    avisos = [f"Falha ao ler e-mails: {erro[:150]}" for erro in r.coleta.erros]
    if r.analise:
        sem_ia = r.analise.por_provedor.get("regras", 0)
        if sem_ia and cfg["llm"]["provider"] != "regras":
            avisos.append(f"{sem_ia} e-mail(s) classificado(s) sem IA, pelas regras.")
    return avisos


def resumo_semanal(cfg: dict, agora: datetime, dry_run: bool = False) -> str:
    conn = storage.conectar(caminho_banco(cfg))
    try:
        texto = resumo.montar(conn, cfg, agora)
        if not dry_run:
            telegram_bot.enviar(texto)
            with conn:
                conn.execute("INSERT INTO execucoes (tipo, iniciada_em, terminada_em, status) "
                             "VALUES ('semanal', ?, ?, 'ok')", (agora.isoformat(), agora.isoformat()))
            log.info("resumo semanal enviado")
        return texto
    finally:
        conn.close()


# --- para a recuperação depois de um reinício ---------------------------------------------------

def relatorio_enviado(conn: sqlite3.Connection, dia) -> bool:
    return conn.execute("SELECT 1 FROM snapshots WHERE data = ?", (dia.isoformat(),)).fetchone() is not None


def resumo_enviado(conn: sqlite3.Connection, dia) -> bool:
    linhas = conn.execute("SELECT iniciada_em FROM execucoes WHERE tipo = 'semanal' AND status = 'ok'").fetchall()
    return any(datetime.fromisoformat(l[0]).date() == dia for l in linhas)
