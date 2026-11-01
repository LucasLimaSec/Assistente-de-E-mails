"""Serviço permanente: bot de comandos + horários do dia, pelo JobQueue do python-telegram-bot.

Horários (fuso de geral.fuso_horario):
  llm.horario_analise   (05:30, dias úteis)  análise dos e-mails, até 5 min antes do relatório
  geral.horario_relatorio (08:00, dias úteis)  relatório; o que chegou depois da análise vai pelas regras
  resumo_semanal.dia/horario (sáb 17:00)      resumo da semana
  a cada 5 min                                  batimento em data/batimento (healthcheck do container)

Se o container reiniciar no meio do dia, o que ficou para trás hoje roda logo ao subir.
Uma trava impede a análise e o relatório de rodarem juntos (os dois gravam no banco e usam o Ollama).
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src import rotinas, storage, telegram_bot
from src.config import DIAS_SEMANA, caminho_banco

log = logging.getLogger("assistente.agendador")
ESPERA_TRAVA = 30 * 60          # o relatório espera a análise terminar por até 30 min
INTERVALO_BATIMENTO = 300
ARQUIVO_BATIMENTO = "batimento"


def dias_ptb(nomes: list[str]) -> tuple[int, ...]:
    """'seg'...'dom' -> números do JobQueue, onde 0 é domingo (diferente do date.weekday())."""
    return tuple(sorted((DIAS_SEMANA.index(n) + 1) % 7 for n in nomes))


def pendentes_ao_iniciar(cfg: dict, agora: datetime, conn) -> list[str]:
    """O que deveria ter rodado hoje antes de `agora` e não rodou (container reiniciado, servidor desligado)."""
    tarefas = []
    hoje = agora.date()
    if rotinas.dia_util(cfg, agora):
        hora_analise = rotinas.hora_config(cfg["llm"].get("horario_analise", "05:30"))
        hora_relatorio = rotinas.hora_config(cfg["geral"]["horario_relatorio"])
        if agora.time() >= hora_relatorio:
            if not rotinas.relatorio_enviado(conn, hoje):
                tarefas.append("relatorio")
        elif agora.time() >= hora_analise and agora < rotinas.prazo_da_analise(cfg, agora):
            tarefas.append("analise")
    semanal = cfg.get("resumo_semanal") or {}
    if semanal.get("enabled") and DIAS_SEMANA[agora.weekday()] == semanal.get("dia", "sab") \
            and agora.time() >= rotinas.hora_config(semanal.get("horario", "17:00")) \
            and not rotinas.resumo_enviado(conn, hoje):
        tarefas.append("resumo")
    return tarefas


class Servico:
    """Estado compartilhado entre os horários (trava e avisos para o próximo relatório)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.fuso = ZoneInfo(cfg["geral"]["fuso_horario"])
        self.trava = threading.Lock()
        self.avisos: list[str] = []

    def agora(self) -> datetime:
        return datetime.now(self.fuso)

    # --- trabalhos (rodam numa thread) -----------------------------------------------------------

    def analisar(self) -> None:
        if not self.trava.acquire(blocking=False):
            log.warning("análise pulada: outra rotina está rodando")
            return
        try:
            agora = self.agora()
            r = rotinas.analise_madrugada(self.cfg, self.agora, rotinas.prazo_da_analise(self.cfg, agora))
            if r.erros:
                log.warning("análise com %d aviso(s): %s", len(r.erros), "; ".join(r.erros)[:500])
        except Exception as e:
            log.exception("análise falhou")
            self.avisos.append(f"A análise das {self.cfg['llm'].get('horario_analise', '05:30')} falhou "
                               f"({type(e).__name__}: {str(e)[:120]}).")
        finally:
            self.trava.release()

    def relatar(self) -> None:
        if not self.trava.acquire(timeout=ESPERA_TRAVA):
            self._alerta("⚠️ O relatório de hoje não saiu: a análise ainda estava rodando depois de 30 min.")
            return
        try:
            agora = self.agora()
            # prazo = agora: o que chegou depois da análise vai pelas regras e o relatório sai na hora.
            rotinas.relatorio_diario(self.cfg, agora, prazo_limite=agora, avisos_extra=list(self.avisos))
            self.avisos.clear()
        except telegram_bot.ErroTelegram as e:
            log.error("relatório não enviado: %s", e)
        except Exception as e:
            log.exception("relatório falhou")
            self._alerta(f"⚠️ O relatório de hoje falhou ({type(e).__name__}: {str(e)[:200]}). "
                         "Veja logs/assistente.log no servidor.")
        finally:
            self.trava.release()

    def resumir(self) -> None:
        try:
            rotinas.resumo_semanal(self.cfg, self.agora())
        except telegram_bot.ErroTelegram as e:
            log.error("resumo semanal não enviado: %s", e)
        except Exception as e:
            log.exception("resumo semanal falhou")
            self._alerta(f"⚠️ O resumo semanal falhou ({type(e).__name__}: {str(e)[:200]}).")

    def bater(self) -> None:
        caminho = Path(caminho_banco(self.cfg)).parent / ARQUIVO_BATIMENTO
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(self.agora().isoformat(), encoding="utf-8")

    def recuperar(self) -> list[str]:
        conn = storage.conectar(caminho_banco(self.cfg))
        try:
            tarefas = pendentes_ao_iniciar(self.cfg, self.agora(), conn)
        finally:
            conn.close()
        if tarefas:
            log.info("recuperando ao iniciar: %s", ", ".join(tarefas))
        for tarefa in tarefas:
            {"analise": self.analisar, "relatorio": self.relatar, "resumo": self.resumir}[tarefa]()
        return tarefas

    def _alerta(self, texto: str) -> None:
        try:
            telegram_bot.enviar(texto)
        except telegram_bot.ErroTelegram as e:
            log.error("aviso de falha não enviado: %s", e)


def _em_thread(funcao):
    async def callback(context):
        await asyncio.to_thread(funcao)
    return callback


def agendar(app, servico: Servico) -> None:
    cfg, fuso = servico.cfg, servico.fuso
    dias = dias_ptb(cfg["geral"]["dias_uteis"])
    jq = app.job_queue
    hora_analise = rotinas.hora_config(cfg["llm"].get("horario_analise", "05:30")).replace(tzinfo=fuso)
    hora_relatorio = rotinas.hora_config(cfg["geral"]["horario_relatorio"]).replace(tzinfo=fuso)
    jq.run_daily(_em_thread(servico.analisar), hora_analise, days=dias, name="análise")
    jq.run_daily(_em_thread(servico.relatar), hora_relatorio, days=dias, name="relatório")
    semanal = cfg.get("resumo_semanal") or {}
    if semanal.get("enabled"):
        hora_resumo = rotinas.hora_config(semanal.get("horario", "17:00")).replace(tzinfo=fuso)
        jq.run_daily(_em_thread(servico.resumir), hora_resumo, days=dias_ptb([semanal.get("dia", "sab")]),
                     name="resumo semanal")
    jq.run_repeating(_em_thread(servico.bater), interval=INTERVALO_BATIMENTO, first=1, name="batimento")
    jq.run_once(_em_thread(servico.recuperar), when=10, name="recuperação")


def proximos(app) -> list[tuple[str, datetime]]:
    return sorted(((j.name, j.next_t) for j in app.job_queue.jobs()
                   if j.next_t and j.name not in ("batimento", "recuperação", "anúncio")), key=lambda x: x[1])


def montar_servico(cfg: dict):
    from src import comandos

    servico = Servico(cfg)
    app = None

    async def status(update, context):
        agenda = [(nome, quando.astimezone(servico.fuso)) for nome, quando in proximos(app)]
        texto = await asyncio.to_thread(telegram_bot._com_banco, cfg, comandos.status, agenda, servico.agora())
        await telegram_bot._responder(update, texto)

    app = telegram_bot.montar_aplicacao(cfg, extras=[("status", "últimas execuções e próximos horários", status)])
    agendar(app, servico)

    async def anunciar(context):
        for nome, quando in proximos(app):
            log.info("agendado: %s em %s", nome, quando.astimezone(servico.fuso).strftime("%d/%m %H:%M"))

    app.job_queue.run_once(anunciar, when=2, name="anúncio")
    return app, servico


def rodar_servico(cfg: dict) -> None:
    app, _ = montar_servico(cfg)
    log.info("serviço no ar: bot + horários do config")
    app.run_polling(allowed_updates=["message"])
