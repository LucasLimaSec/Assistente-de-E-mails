import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from src import agendador, comandos, fluxo, kanban, logs, rotinas, storage, telegram_bot
from src.config import caminho_banco
from src.email_reader import Coleta
from src.kanban import Resumo

SP = ZoneInfo("America/Sao_Paulo")
SEG_0530 = datetime(2026, 11, 2, 5, 30, tzinfo=SP)


@pytest.fixture
def cfg(tmp_path):
    texto = (Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(texto)
    cfg["_pasta"] = tmp_path
    return cfg


@pytest.fixture
def conn(cfg):
    conn = storage.conectar(caminho_banco(cfg))
    yield conn
    conn.close()


def as_(dia, hora, minuto=0):
    """Data de novembro/2026 (2 = segunda) às hh:mm em São Paulo."""
    return datetime(2026, 11, dia, hora, minuto, tzinfo=SP)


# --- horários -----------------------------------------------------------------------------------

def test_dias_no_formato_do_jobqueue():
    assert agendador.dias_ptb(["seg", "ter", "qua", "qui", "sex", "sab"]) == (1, 2, 3, 4, 5, 6)
    assert agendador.dias_ptb(["dom", "sab"]) == (0, 6)


def test_prazo_da_analise_e_dia_util(cfg):
    assert rotinas.prazo_da_analise(cfg, SEG_0530) == as_(2, 7, 55)
    assert rotinas.dia_util(cfg, as_(7, 9)) and not rotinas.dia_util(cfg, as_(8, 9))   # sáb sim, dom não


@pytest.mark.parametrize("quando, esperado", [
    (as_(2, 4), []),                       # antes da análise
    (as_(2, 6), ["analise"]),              # reiniciou durante a janela da análise
    (as_(2, 7, 58), []),                   # entre o prazo da análise e o relatório
    (as_(2, 9), ["relatorio"]),            # passou das 08:00 e não enviou
    (as_(8, 10), []),                      # domingo
    (as_(7, 18), ["relatorio", "resumo"]),  # sábado depois das 17:00, nada enviado
    (as_(7, 16), ["relatorio"]),
])
def test_recuperacao_ao_iniciar(cfg, conn, quando, esperado):
    assert agendador.pendentes_ao_iniciar(cfg, quando, conn) == esperado


def test_recuperacao_nao_repete_o_que_ja_foi_enviado(cfg, conn):
    kanban.salvar_snapshot(conn, as_(7, 8).date(), {c: [] for c in kanban.COLUNAS})
    conn.execute("INSERT INTO execucoes (tipo, iniciada_em, status) VALUES ('semanal', ?, 'ok')",
                 (as_(7, 17).isoformat(),))
    conn.commit()
    assert agendador.pendentes_ao_iniciar(cfg, as_(7, 18), conn) == []


# --- análise da madrugada -----------------------------------------------------------------------

class Relogio:
    def __init__(self, inicio, passo=timedelta(minutes=30)):
        self.atual, self.passo = inicio, passo

    def __call__(self, tz=None):
        valor = self.atual
        self.atual += self.passo
        return valor


def resultado(para_ia, erros=()):
    return fluxo.ResultadoFluxo(Coleta(), None, Resumo.vazio(), para_ia=para_ia, erros=list(erros))


def test_madrugada_repete_enquanto_chegam_emails(cfg, monkeypatch):
    rodadas = iter([resultado(6), resultado(2), resultado(0)])
    chamadas = []

    def atualizar(cfg_, conn, inicio, agora, prazo_limite=None):
        chamadas.append((agora, prazo_limite))
        return next(rodadas)

    monkeypatch.setattr(fluxo, "atualizar_quadro", atualizar)
    r = rotinas.analise_madrugada(cfg, Relogio(SEG_0530), as_(2, 7, 55))
    assert (r.rodadas, r.analisados) == (3, 8)
    assert all(prazo == as_(2, 7, 55) for _, prazo in chamadas)


def test_madrugada_para_no_prazo(cfg, monkeypatch):
    monkeypatch.setattr(fluxo, "atualizar_quadro", lambda *a, **k: resultado(6))
    r = rotinas.analise_madrugada(cfg, Relogio(SEG_0530, timedelta(hours=1)), as_(2, 7, 55))
    assert r.rodadas == 3          # 05:30, 06:30, 07:30; às 08:30 o prazo já passou


# --- serviço: relatório, falhas e trava ---------------------------------------------------------

@pytest.fixture
def servico(cfg, monkeypatch):
    s = agendador.Servico(cfg)
    s.enviados = []
    monkeypatch.setattr(telegram_bot, "enviar", lambda texto: s.enviados.append(texto) or 1)
    monkeypatch.setattr(s, "agora", lambda: as_(2, 8))
    return s


def test_relatorio_leva_avisos_da_analise_e_limpa(servico, monkeypatch):
    pedidos = []

    def relatorio(cfg, agora, prazo_limite=None, avisos_extra=None):
        pedidos.append((prazo_limite, avisos_extra))

    monkeypatch.setattr(rotinas, "relatorio_diario", relatorio)
    servico.avisos.append("A análise das 05:30 falhou (ErroIA: x).")
    servico.relatar()
    assert pedidos == [(as_(2, 8), ["A análise das 05:30 falhou (ErroIA: x)."])]   # prazo = agora: regras
    assert servico.avisos == []


def test_analise_que_quebra_vira_aviso_no_relatorio(servico, monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("IMAP caiu")

    monkeypatch.setattr(rotinas, "analise_madrugada", quebra)
    servico.analisar()
    assert servico.avisos == ["A análise das 05:30 falhou (RuntimeError: IMAP caiu)."]
    assert servico.enviados == []          # nada de mensagem às 05:30
    assert not servico.trava.locked()


def test_relatorio_que_quebra_avisa_no_telegram(servico, monkeypatch, caplog):
    def quebra(*a, **k):
        raise ValueError("banco corrompido")

    monkeypatch.setattr(rotinas, "relatorio_diario", quebra)
    with caplog.at_level(logging.ERROR):
        servico.relatar()
    assert servico.enviados == ["⚠️ O relatório de hoje falhou (ValueError: banco corrompido). "
                                "Veja logs/assistente.log no servidor."]
    assert "relatório falhou" in caplog.text and not servico.trava.locked()


def test_relatorio_espera_a_analise(servico, monkeypatch):
    monkeypatch.setattr(agendador, "ESPERA_TRAVA", 0.05)
    monkeypatch.setattr(rotinas, "relatorio_diario", lambda *a, **k: None)
    servico.trava.acquire()                 # análise ainda rodando
    try:
        servico.relatar()
    finally:
        servico.trava.release()
    assert "ainda estava rodando" in servico.enviados[0]


def test_analise_nao_roda_em_paralelo(servico, monkeypatch):
    chamadas = []
    monkeypatch.setattr(rotinas, "analise_madrugada", lambda *a, **k: chamadas.append(1))
    servico.trava.acquire()
    try:
        servico.analisar()
    finally:
        servico.trava.release()
    assert chamadas == []


def test_recuperar_roda_o_que_faltou(servico, monkeypatch):
    feitos = []
    monkeypatch.setattr(servico, "agora", lambda: as_(7, 18))
    monkeypatch.setattr(servico, "relatar", lambda: feitos.append("relatorio"))
    monkeypatch.setattr(servico, "resumir", lambda: feitos.append("resumo"))
    assert servico.recuperar() == ["relatorio", "resumo"] and feitos == ["relatorio", "resumo"]


def test_batimento(servico, cfg):
    servico.bater()
    assert (Path(caminho_banco(cfg)).parent / "batimento").read_text().startswith("2026-11-02T08:00")


def test_resumo_semanal_registrado(cfg, monkeypatch):
    enviados = []
    monkeypatch.setattr(telegram_bot, "enviar", lambda texto: enviados.append(texto) or 1)
    rotinas.resumo_semanal(cfg, as_(7, 17))
    conn = storage.conectar(caminho_banco(cfg))
    try:
        assert rotinas.resumo_enviado(conn, as_(7, 17).date())
    finally:
        conn.close()
    assert "Resumo da semana" in enviados[0]


# --- montagem do serviço ------------------------------------------------------------------------

def test_servico_agenda_os_horarios(cfg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    app, servico = agendador.montar_servico(cfg)
    jobs = {j.name: j for j in app.job_queue.jobs()}
    assert {"análise", "relatório", "resumo semanal", "batimento", "recuperação", "anúncio"} <= set(jobs)
    comandos_registrados = {c for grupo in app.handlers.values() for h in grupo
                            for c in getattr(h, "commands", ())}
    assert "status" in comandos_registrados


def test_status(conn):
    conn.execute("INSERT INTO execucoes (tipo, iniciada_em, status, tokens_entrada, tokens_saida, erro) "
                 "VALUES ('diario', ?, 'falha', 10, 2, 'IMAP <caiu>')", (as_(2, 5, 30).isoformat(),))
    conn.commit()
    texto = comandos.status(conn, [("relatório", as_(3, 8))], as_(2, 9))
    assert "❌ seg 02/11 05:30 diario · tokens 10/2 · erro: IMAP &lt;caiu&gt;" in texto
    assert "Último relatório enviado: nenhum ainda" in texto and "• ter 03/11 08:00 relatório" in texto


# --- logs ---------------------------------------------------------------------------------------

def test_log_em_arquivo_com_rotacao(tmp_path):
    raiz = logging.getLogger()
    antes = list(raiz.handlers)
    try:
        caminho = logs.configurar_arquivo(tmp_path / "logs")
        logging.getLogger("assistente.teste").warning("olá log")
        for h in raiz.handlers:
            h.flush()
        assert "olá log" in caminho.read_text(encoding="utf-8")
    finally:
        for h in raiz.handlers[:]:
            if h not in antes:
                raiz.removeHandler(h)
                h.close()


def test_log_sem_permissao_nao_derruba(tmp_path):
    bloqueada = tmp_path / "arquivo"
    bloqueada.write_text("não é pasta")
    assert logs.configurar_arquivo(bloqueada / "logs") is None
