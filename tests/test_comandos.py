from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from telegram import Chat, Message, MessageEntity, Update

from src import agenda, comandos, kanban, report, resumo, storage
from src.email_reader import buscar_email
from src.llm.base import ErroIA
from tests import test_email_reader as ter
from tests.test_kanban import CFG, SEG, analise, criar_simples, email_
from tests.test_report import colunas

CFG_COMPLETO = {**CFG, "geral": {**CFG["geral"], "horario_relatorio": "08:00", "fuso_horario": "America/Sao_Paulo"},
                "llm": {"provider": "ollama"}}


@pytest.fixture
def conn(tmp_path):
    conn = storage.conectar(tmp_path / "teste.db")
    yield conn
    conn.close()


# --- mover, nova, nota, quadro ------------------------------------------------------------------

def test_feito_varios_e_erros(conn):
    criar_simples(conn), criar_simples(conn)
    resposta = comandos.mover(conn, "feito", ["1", "#2,", "99"], SEG)
    assert resposta.splitlines() == ["✅ #1 Emitir nota → Feito", "✅ #2 Emitir nota → Feito", "❌ #99 não existe."]
    assert kanban.obter(conn, 1).coluna == "feito"
    assert comandos.mover(conn, "feito", ["1"], SEG) == "#1 já estava em Feito."
    assert "não é um número" in comandos.mover(conn, "feito", ["abc"], SEG)
    assert comandos.mover(conn, "andamento", [], SEG).startswith("Diga o número")


def test_mover_para_cada_coluna(conn):
    t = criar_simples(conn)
    for comando, coluna in comandos.MOVER.items():
        comandos.mover(conn, comando, [str(t)], SEG)
        assert kanban.obter(conn, t).coluna == coluna


def test_nova_tarefa_e_prioridade_alta(conn):
    assert comandos.nova(conn, "Ligar para o contador", SEG) == "🟡 Criada a tarefa #1: Ligar para o contador"
    assert comandos.nova(conn, "!  Pagar   DARF", SEG) == "🔴 Criada a tarefa #2: Pagar DARF"
    t = kanban.obter(conn, 2)
    assert (t.origem, t.tipo, t.prioridade, t.coluna) == ("telegram", "realizar", "alta", "a_fazer")
    assert comandos.nova(conn, "  ", SEG).startswith("Escreva a tarefa")
    assert "&lt;b&gt;" in comandos.nova(conn, "<b>x</b>", SEG)


def test_nota_aparece_no_relatorio_seguinte(conn):
    comandos.nota(conn, "Fechei o orçamento com o cliente X", SEG)
    notas = kanban.notas_desde(conn, SEG - timedelta(hours=1))
    assert notas == ["Fechei o orçamento com o cliente X"]
    texto = report.montar(report.DadosRelatorio(SEG + timedelta(days=1), SEG, colunas(), notas=notas))
    assert "✅ FEITO ONTEM</b>\n• 📝 Fechei o orçamento com o cliente X" in texto
    assert kanban.notas_desde(conn, SEG + timedelta(hours=1)) == []


def test_comando_altera_banco_e_aparece_no_relatorio(conn):
    e = email_(1)
    kanban.aplicar(conn, [e], [analise(e)], SEG)
    comandos.mover(conn, "feito", ["1"], SEG + timedelta(hours=2))
    comandos.nova(conn, "Ligar para o contador", SEG + timedelta(hours=3))
    amanha = SEG + timedelta(days=1)
    dados = report.DadosRelatorio(amanha, SEG, kanban.quadro(conn, amanha, CFG, SEG))
    texto = report.montar(dados)
    assert "✅ FEITO ONTEM</b>\n• Aprovar orçamento da Maria" in texto
    assert "🟡 #2 Realizar: Ligar para o contador" in texto


def test_quadro_mostra_colunas_vazias(conn):
    criar_simples(conn)
    texto = comandos.quadro(conn, CFG_COMPLETO, SEG)
    assert "Quadro de segunda, 02/11" in texto and "#1 Realizar: Emitir nota" in texto
    assert "🔄 EM ANDAMENTO</b>\n(vazio)" in texto


# --- rascunho -----------------------------------------------------------------------------------

class ProvedorRascunho:
    nome = "falso"

    def __init__(self, erro=None):
        self.erro, self.pedidos = erro, []

    def rascunhar(self, email, contexto):
        if self.erro:
            raise ErroIA(self.erro)
        self.pedidos.append(email.message_id)
        return "  Olá Maria, segue o orçamento.  ", 100, 20


def tarefa_de_email(conn):
    e = email_(1)
    kanban.aplicar(conn, [e], [analise(e)], SEG)
    return e


def test_rascunho_gera_guarda_e_reaproveita(conn):
    e = tarefa_de_email(conn)
    n, pronta, gerar = comandos.preparar_rascunho(conn, ["1"])
    assert (n, pronta, gerar) == (1, None, True)

    provedor = ProvedorRascunho()
    texto = comandos.gerar_rascunho(conn, CFG_COMPLETO, 1, SEG, lambda cfg, conta, mid: e, provedor)
    assert texto.endswith("Olá Maria, segue o orçamento.") and provedor.pedidos == [e.message_id]
    assert kanban.obter(conn, 1).rascunho == "Olá Maria, segue o orçamento."

    # Segunda vez: vem do banco, sem IA. "novo" pede outro.
    n, pronta, gerar = comandos.preparar_rascunho(conn, ["1"])
    assert not gerar and "Guardado de antes" in pronta
    assert comandos.preparar_rascunho(conn, ["1", "novo"])[2] is True


def test_rascunho_casos_sem_ia(conn):
    criar_simples(conn)        # #1 criada pelo Telegram, sem e-mail
    assert "não veio de um e-mail" in comandos.preparar_rascunho(conn, ["1"])[1]
    assert "não existe" in comandos.preparar_rascunho(conn, ["9"])[1]
    assert comandos.preparar_rascunho(conn, [])[1].startswith("Diga o número")


def test_rascunho_erros_da_ia_e_do_email(conn):
    tarefa_de_email(conn)
    assert "não achei mais" in comandos.gerar_rascunho(
        conn, CFG_COMPLETO, 1, SEG, lambda *a: None, ProvedorRascunho()).lower()
    e = email_(1)
    assert "não conseguiu gerar" in comandos.gerar_rascunho(
        conn, CFG_COMPLETO, 1, SEG, lambda *a: e, ProvedorRascunho(erro="tempo esgotado"))
    assert kanban.obter(conn, 1).rascunho is None


def test_buscar_email_pelo_message_id(monkeypatch):
    monkeypatch.setenv("EMAIL_TESTE", ter.PROPRIO)
    monkeypatch.setenv("SENHA_TESTE", "senha")
    imap = ter.FakeIMAP()
    cfg = {"contas": [ter.CONTA], "filtros": {}, "privacidade": {"max_caracteres_corpo": 1500}}
    e = buscar_email(cfg, "pessoal", '<resposta-2@example.com>"x', conectar=lambda *a, **k: imap)
    busca = next(args for comando, args in imap.comandos if comando == "SEARCH")
    assert busca == ("HEADER", "Message-ID", '"<resposta-2@example.com>x"')   # aspas removidas
    assert e is not None and imap.abertas == [("INBOX", True)]
    assert buscar_email(cfg, "outra", "<x>") is None


# --- resumo semanal -----------------------------------------------------------------------------

def test_resumo_semanal(conn):
    sab = SEG + timedelta(days=5, hours=9)          # sábado 17:00
    feita = criar_simples(conn)
    kanban.mover(conn, feita, "feito", SEG + timedelta(days=2))
    criar_simples(conn, agora=SEG - timedelta(days=3))   # parada desde a sexta anterior: atrasada
    comandos.nota(conn, "Reunião com cliente", SEG)
    conn.execute("INSERT INTO execucoes (tipo, iniciada_em, status, tokens_entrada, tokens_saida) "
                 "VALUES ('diario', ?, 'ok', 12000, 3000)", (SEG.isoformat(),))
    conn.commit()
    texto = resumo.montar(conn, CFG_COMPLETO, sab)
    assert "Resumo da semana</b> (02/11 a 07/11)" in texto
    assert "✅ 1 concluída(s) · 📋 1 pendente(s) · ⚠️ 1 atrasada(s)" in texto
    assert "• #1 Emitir nota (qua)" in texto and "#2 Emitir nota (há 7 dias)" in texto
    assert "📝 1 nota(s)" in texto
    assert "1 execução(ões), 12.000 tokens de entrada / 3.000 de saída, IA local, sem custo." in texto


def test_resumo_estima_custo_do_claude(conn):
    conn.execute("INSERT INTO execucoes (tipo, iniciada_em, status, tokens_entrada, tokens_saida) "
                 "VALUES ('diario', ?, 'ok', 1000000, 100000)", (SEG.isoformat(),))
    conn.commit()
    cfg = {**CFG_COMPLETO, "llm": {"provider": "claude"}}
    assert "custo estimado US$ 0,1500" in resumo.montar(conn, cfg, SEG + timedelta(days=1))


# --- agenda (iCal) ------------------------------------------------------------------------------

SP = ZoneInfo("America/Sao_Paulo")
ICAL = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//teste//
BEGIN:VEVENT
UID:1
DTSTART:20261102T120000Z
DTEND:20261102T130000Z
SUMMARY:Reuni\xc3\xa3o com cliente
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20261103
DTEND;VALUE=DATE:20261104
SUMMARY:Feriado
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;TZID=America/Sao_Paulo:20261026T070000
DTEND;TZID=America/Sao_Paulo:20261026T080000
RRULE:FREQ=DAILY
SUMMARY:Academia
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART:20261103T150000Z
SUMMARY:Cancelado
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""


def test_agenda_eventos_do_dia_com_repeticao_e_dia_inteiro():
    ter_ = datetime(2026, 11, 3, tzinfo=SP)
    hoje = agenda.eventos(ICAL, ter_, ter_ + timedelta(days=1), SP)
    assert [(e.titulo, e.dia_inteiro, e.inicio.strftime("%H:%M")) for e in hoje] == [
        ("Feriado", True, "00:00"), ("Academia", False, "07:00")]


def test_agenda_feitos_desde_o_ultimo_relatorio(monkeypatch):
    monkeypatch.setenv(agenda.VARIAVEL_URL, "https://exemplo.invalid/segredo.ics")
    monkeypatch.setattr(agenda, "baixar", lambda url: ICAL)
    seg_relatorio = datetime(2026, 11, 2, 8, 0, tzinfo=SP)
    feitos, hoje = agenda.feitos_e_hoje(CFG_COMPLETO, seg_relatorio, seg_relatorio + timedelta(days=1))
    # Segunda: academia às 07:00 ficou antes do relatório; reunião às 09:00 (12:00 UTC) entra.
    assert [e.titulo for e in feitos] == ["Reunião com cliente"]
    assert [e.titulo for e in hoje] == ["Feriado", "Academia"]


def test_agenda_invalida_e_erro_de_rede_sem_vazar_url(monkeypatch):
    with pytest.raises(agenda.ErroAgenda, match="formato inválido"):
        agenda.eventos(b"isso nao e ical", SEG, SEG + timedelta(days=1), SP)

    def falha(*a, **k):
        raise OSError("https://exemplo.invalid/segredo.ics recusou")

    monkeypatch.setattr(agenda.urllib.request, "urlopen", falha)
    with pytest.raises(agenda.ErroAgenda) as erro:
        agenda.baixar("https://exemplo.invalid/segredo.ics")
    assert "segredo" not in str(erro.value)


def test_relatorio_com_agenda():
    ev = [agenda.Evento("Feriado", datetime(2026, 11, 3, tzinfo=SP), True),
          agenda.Evento("Academia", datetime(2026, 11, 3, 7, tzinfo=SP))]
    texto = report.montar(report.DadosRelatorio(SEG + timedelta(days=1), SEG, colunas(), agenda_hoje=ev,
                                                agenda_feita=[agenda.Evento("Reunião", SEG)]))
    assert "• 📅 Reunião" in texto
    assert "📅 AGENDA DE HOJE</b>\n• Dia todo: Feriado\n• 07:00 Academia" in texto


# --- bot: só o chat configurado -----------------------------------------------------------------

class BotDeMentira:
    username = "AssistenteBot"


def mensagem(chat_id, texto):
    entidades = [MessageEntity(type="bot_command", offset=0, length=len(texto.split()[0]))] \
        if texto.startswith("/") else []
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="private"),
                  text=texto, entities=entidades)
    msg.set_bot(BotDeMentira())
    return Update(update_id=1, message=msg)


def handlers_que_atendem(app, update):
    return [h for grupo in app.handlers.values() for h in grupo if h.check_update(update)]


def test_bot_ignora_outros_chats(monkeypatch):
    from src.telegram_bot import montar_aplicacao

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    app = montar_aplicacao(CFG_COMPLETO)
    for texto in ("/feito 1", "/nova invasor", "texto livre", "/qualquer"):
        assert handlers_que_atendem(app, mensagem(42, texto)), texto
        assert handlers_que_atendem(app, mensagem(666, texto)) == [], texto
