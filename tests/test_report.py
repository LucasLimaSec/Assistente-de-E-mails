from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import telegram.error

import main
from src import fluxo, kanban, report, storage, telegram_bot
from src.email_reader import Coleta
from src.kanban import Tarefa
from src.report import DadosRelatorio, Oportunidade
from tests.test_kanban import analise, email_

BRT = timezone(timedelta(hours=-3))
QUA = datetime(2026, 10, 28, 8, 0, tzinfo=BRT)


def tarefa(id, titulo, coluna="a_fazer", tipo="responder", prioridade="media", prazo=None, atrasada=False, dias=0,
           conta="pessoal"):
    return Tarefa(id=id, tipo=tipo, titulo=titulo, coluna=coluna, prioridade=prioridade, origem="email", conta=conta,
                  remetente=None, prazo=prazo, criada_em=QUA, coluna_desde=QUA, concluida_em=None,
                  dias_parada=dias, atrasada=atrasada)


def colunas(*tarefas):
    c = {k: [] for k in kanban.COLUNAS}
    for t in tarefas:
        c[t.coluna].append(t)
    return c


def test_relatorio_no_formato_do_exemplo():
    dados = DadosRelatorio(
        agora=QUA, desde=QUA - timedelta(days=1),
        colunas=colunas(
            tarefa(3, "Respondeu João sobre o contrato", "feito"),
            tarefa(12, "Maria – aprovar orçamento", prioridade="alta", prazo="2026-10-28"),
            tarefa(14, "Emitir nota fiscal do cliente X", tipo="realizar"),
            tarefa(15, "Convite para webinar", prioridade="baixa"),
            tarefa(9, "Ligar para o contador", tipo="realizar", atrasada=True, dias=4),
            tarefa(7, "Proposta para empresa Y", "em_andamento"),
            tarefa(5, "Retorno do fornecedor sobre prazo", "aguardando"),
        ),
        oportunidades=[Oportunidade("Logo para padaria", 1), Oportunidade("Pentest em API", 9)],
    )
    texto = report.montar(dados)
    assert texto == """☀️ <b>Bom dia! Quarta, 28/10</b>
3 para responder · 2 para realizar · 1 atrasada

<b>✅ FEITO ONTEM</b>
• Respondeu João sobre o contrato

<b>📋 A FAZER</b>
🔴 #12 Responder: Maria – aprovar orçamento (hoje)
🟡 #14 Realizar: Emitir nota fiscal do cliente X
🟢 #15 Responder: Convite para webinar
⚠️ #9 Realizar: Ligar para o contador (há 4 dias)

<b>🔄 EM ANDAMENTO</b>
• #7 Proposta para empresa Y

<b>⏳ AGUARDANDO</b>
• #5 Retorno do fornecedor sobre prazo

<b>💼 OPORTUNIDADES</b>
• 9/10 Pentest em API
• 1/10 Logo para padaria

💡 <b>Foco do dia:</b> #12 Maria – aprovar orçamento (hoje).

<i>Comandos: /feito 12 · /nova texto · /quadro · /ajuda</i>"""


def test_limite_por_coluna_e_resto():
    tarefas = [tarefa(i, f"Tarefa {i}") for i in range(1, 9)]
    texto = report.montar(DadosRelatorio(QUA, QUA - timedelta(days=1), colunas(*tarefas)), max_itens=5)
    assert "#5 " in texto and "#6 " not in texto and "+3 tarefa(s)" in texto


def test_oportunidades_limitadas():
    ops = [Oportunidade(f"Projeto {i}", i) for i in range(8)]
    texto = report.montar(DadosRelatorio(QUA, QUA, colunas(), ops), max_oportunidades=5)
    assert "• 7/10 Projeto 7" in texto and "Projeto 2" not in texto and "+3 oportunidade(s)" in texto


def test_quadro_vazio_e_segunda_feira():
    segunda = datetime(2026, 11, 2, 8, 0, tzinfo=BRT)
    sabado = datetime(2026, 10, 31, 8, 0, tzinfo=BRT)
    texto = report.montar(DadosRelatorio(segunda, sabado, colunas(tarefa(1, "Feita no sábado", "feito"))))
    assert "Bom dia! Segunda, 02/11</b>\nQuadro em dia" in texto
    assert "✅ FEITO DESDE SÁBADO" in texto and "Nada pendente" in texto and "Foco do dia" not in texto


def test_prazos():
    hoje = QUA.date()
    rotulos = [report._prazo(tarefa(1, "x", prazo=p), hoje) for p in
               ("2026-10-27", "2026-10-28", "2026-10-29", "2026-11-02", None)]
    assert rotulos == [" (venceu 27/10)", " (hoje)", " (amanhã)", " (até seg 02/11)", ""]


def test_foco_prefere_prazo_depois_atraso_depois_prioridade():
    hoje = QUA.date()
    alta = tarefa(1, "Alta", prioridade="alta")
    atrasada = tarefa(2, "Atrasada", prioridade="baixa", atrasada=True, dias=5)
    vence_hoje = tarefa(3, "Vence hoje", prioridade="baixa", prazo="2026-10-28")
    assert report.foco_do_dia([alta, atrasada, vence_hoje], hoje).startswith("#3 ")
    assert report.foco_do_dia([alta, atrasada], hoje).startswith("#2 ")
    assert report.foco_do_dia([alta], hoje).startswith("#1 ")


def test_escapa_html_e_marca_conta_com_varias_contas():
    t = tarefa(1, "Revisar <script> & cia")
    texto = report.montar(DadosRelatorio(QUA, QUA, colunas(t), marcar_conta=True, avisos=["IA <fora>"]))
    assert "Revisar &lt;script&gt; &amp; cia [pessoal]" in texto
    assert "⚠️ IA &lt;fora&gt;" in texto


def test_dividir_respeita_limite_e_secoes():
    secoes = [f"<b>Seção {i}</b>\n" + "linha\n" * 30 for i in range(10)]
    texto = "\n\n".join(s.strip() for s in secoes)
    partes = report.dividir(texto, limite=500)
    assert all(len(p) <= 500 for p in partes)
    assert "\n\n".join(partes) == texto
    assert report.dividir("curto") == ["curto"]
    gigante = "\n".join(["x" * 50] * 30)
    assert all(len(p) <= 200 for p in report.dividir(gigante, limite=200))


# --- envio --------------------------------------------------------------------------------------

class BotFalso:
    enviados = []
    erro = None

    def __init__(self, token):
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send_message(self, **kwargs):
        if BotFalso.erro:
            raise BotFalso.erro
        BotFalso.enviados.append(kwargs)


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-teste")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    BotFalso.enviados, BotFalso.erro = [], None
    return BotFalso


def test_envia_em_html_para_o_chat_configurado(bot):
    assert telegram_bot.enviar("<b>oi</b>", bot_factory=bot) == 1
    msg = bot.enviados[0]
    assert (msg["chat_id"], msg["text"], msg["parse_mode"]) == ("123", "<b>oi</b>", "HTML")


def test_erro_do_telegram_vira_mensagem_clara(bot):
    bot.erro = telegram.error.Forbidden("blocked")
    with pytest.raises(telegram_bot.ErroTelegram, match="bloqueado"):
        telegram_bot.enviar("oi", bot_factory=bot)


def test_sem_credenciais(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(telegram_bot.ErroTelegram, match="TELEGRAM_BOT_TOKEN"):
        telegram_bot.enviar("oi")


# --- fluxo com --relatorio ----------------------------------------------------------------------

@pytest.fixture
def ambiente(tmp_path, monkeypatch, bot):
    """config real numa pasta temporária, coleta falsa e análise falsa (sem IMAP nem IA)."""
    (tmp_path / "config.yaml").write_text(
        (Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    agora = datetime.now(timezone.utc).astimezone()
    e = email_(1, quando=agora)
    original = fluxo.atualizar_quadro

    def atualizar_falso(cfg, conn, inicio, agora_, **kw):
        def analisar_falso(emails, cfg, contexto, prazo_limite=None):
            return fluxo.ResultadoAnalise(analises=[analise(x, prioridade="alta") for x in emails])
        return original(cfg, conn, inicio, agora_, coletar_fn=lambda *a: Coleta(emails=[e]),
                        analisar_fn=analisar_falso)

    enviar_original = telegram_bot.enviar
    monkeypatch.setattr(fluxo, "atualizar_quadro", atualizar_falso)
    monkeypatch.setattr(telegram_bot, "enviar", lambda texto: enviar_original(texto, bot_factory=bot))
    return tmp_path


def contar_tarefas(pasta):
    conn = storage.conectar(pasta / "data" / "assistente.db")
    try:
        return conn.execute("SELECT COUNT(*) FROM tarefas").fetchone()[0]
    finally:
        conn.close()


def test_dry_run_mostra_e_nao_grava_nem_envia(ambiente, bot, capsys):
    assert main.relatorio(str(ambiente / "config.yaml"), 24, None, dry_run=True) == 0
    saida = capsys.readouterr().out
    assert "🔴 #1 Responder: Aprovar orçamento da Maria" in saida
    assert bot.enviados == [] and contar_tarefas(ambiente) == 0


def test_relatorio_envia_grava_e_guarda_retrato(ambiente, bot):
    assert main.relatorio(str(ambiente / "config.yaml"), 24, None, dry_run=False) == 0
    assert len(bot.enviados) == 1 and "Aprovar orçamento da Maria" in bot.enviados[0]["text"]
    assert contar_tarefas(ambiente) == 1
    conn = storage.conectar(ambiente / "data" / "assistente.db")
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    conn.close()
