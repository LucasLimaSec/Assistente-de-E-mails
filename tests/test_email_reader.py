import email
from datetime import datetime, timedelta, timezone
from email import policy
from pathlib import Path

import pytest

from src import email_reader
from src.email_reader import (Coleta, coletar, coletar_conta, corresponde, ler_internaldate, limpar_corpo,
                              mascarar_numeros, motivo_descarte)

FIXTURES = Path(__file__).parent / "fixtures"
BRT = timezone(timedelta(hours=-3))
INICIO = datetime(2026, 10, 30, 0, 0, tzinfo=BRT)
FIM = datetime(2026, 10, 30, 23, 0, tzinfo=BRT)
PROPRIO = "voce@example.org"
CONTA = {"nome": "pessoal", "tipo": "imap", "servidor": "imap.example.org", "porta": 993,
         "endereco_env": "EMAIL_TESTE", "senha_env": "SENHA_TESTE"}

# uid -> (INTERNALDATE, arquivo)
CAIXA = {
    "11": ("30-Oct-2026 09:12:00 -0300", "pessoal_orcamento.eml"),
    "12": ("30-Oct-2026 06:00:00 -0300", "newsletter.eml"),
    "13": ("30-Oct-2026 10:30:00 -0300", "freelancer.eml"),
    "14": ("30-Oct-2026 07:45:00 -0300", "sistema_noreply.eml"),
    "15": ("30-Oct-2026 11:00:00 -0300", "resposta_automatica.eml"),
    "16": ("30-Oct-2026 14:00:00 -0300", "html_somente.eml"),
    # Mesmo dia do SINCE, mas antes do início da janela: deve ser ignorado.
    "10": ("29-Oct-2026 23:30:00 -0300", "pessoal_orcamento.eml"),
}


class FakeIMAP:
    """Imita as respostas do imaplib para SEARCH e FETCH."""

    def __init__(self, gmail=True, promocoes=()):
        self.capabilities = ("IMAP4REV1", "X-GM-EXT-1") if gmail else ("IMAP4REV1",)
        self.promocoes = list(promocoes)
        self.comandos = []
        self.readonly = None
        self.saiu = False

    def login(self, usuario, senha):
        return "OK", [b"logado"]

    def select(self, caixa, readonly=False):
        self.readonly = readonly
        return "OK", [str(len(CAIXA)).encode()]

    def uid(self, comando, *args):
        self.comandos.append((comando, args))
        if comando == "SEARCH":
            uids = self.promocoes if args[0] == "X-GM-RAW" else list(CAIXA)
            return "OK", [" ".join(uids).encode()]
        uids, partes = args
        dados = []
        for uid in uids.split(","):
            interno, arquivo = CAIXA[uid]
            bruto = (FIXTURES / arquivo).read_bytes()
            if "HEADER" in partes:
                conteudo = bruto.split(b"\n\n", 1)[0] + b"\n\n"
                meta = f'{uid} (UID {uid} INTERNALDATE "{interno}" BODY[HEADER] {{{len(conteudo)}}}'
            else:
                conteudo = bruto
                meta = f"{uid} (UID {uid} BODY[]<0> {{{len(conteudo)}}}"
            dados += [(meta.encode(), conteudo), b")"]
        return "OK", dados

    def logout(self):
        self.saiu = True


def rodar(filtros=None, **kwargs_imap):
    imap = FakeIMAP(**kwargs_imap)
    cfg = {"filtros": filtros or {}, "privacidade": {"max_caracteres_corpo": 1500}}
    coleta = Coleta()
    coletar_conta(CONTA, PROPRIO, "senha", INICIO, FIM, cfg, coleta, conectar=lambda *a, **k: imap)
    return coleta, imap


def por_assunto(coleta):
    return {e.assunto: e for e in coleta.emails}


def motivos(coleta):
    return {d.assunto: d.motivo for d in coleta.descartados}


def test_somente_leitura():
    _, imap = rodar()
    assert imap.readonly is True
    fetches = [args[1] for comando, args in imap.comandos if comando == "FETCH"]
    assert fetches and all("PEEK" in partes for partes in fetches)
    assert imap.saiu


def test_filtra_newsletter_automaticos_e_janela():
    coleta, _ = rodar()
    assert set(por_assunto(coleta)) == {"Re: Aprovar orçamento do projeto", "Reunião de amanhã"}
    assert motivos(coleta) == {
        "Black Friday antecipada: 50% OFF": "newsletter",
        "Nova proposta para o seu perfil: Site institucional": "automático",
        "Seu extrato está disponível": "automático",
        "Ausente: Proposta comercial": "automático",
    }
    # A mensagem das 23:30 do dia anterior fica fora da janela e nem é contada.
    assert len(coleta.emails) + len(coleta.descartados) == 6


def test_categoria_promocoes_do_gmail():
    coleta, _ = rodar(promocoes=["12"])
    assert motivos(coleta)["Black Friday antecipada: 50% OFF"] == "promoção"


def test_sem_gmail_nao_busca_categorias():
    _, imap = rodar(gmail=False)
    assert not any(args and args[0] == "X-GM-RAW" for _, args in imap.comandos)


def test_incluir_sempre_e_vip_passam_pelos_filtros():
    coleta, _ = rodar({"incluir_sempre": ["@freela.example.com"], "vip": ["@empresa.example.com"]})
    emails = por_assunto(coleta)
    assert "Nova proposta para o seu perfil: Site institucional" in emails
    assert emails["Reunião de amanhã"].vip
    assert not emails["Re: Aprovar orçamento do projeto"].vip


def test_excluir_vence_vip():
    coleta, _ = rodar({"excluir_remetentes": ["carlos@empresa.example.com"], "vip": ["@empresa.example.com"]})
    assert motivos(coleta)["Reunião de amanhã"] == "excluído"


def test_corpo_limpo_e_mascarado():
    coleta, _ = rodar()
    e = por_assunto(coleta)["Re: Aprovar orçamento do projeto"]
    assert e.remetente == "maria.souza@example.com"
    assert e.remetente_nome == "Maria Souza"
    assert e.conversa == "<original-1@example.org>"
    assert "aprovação do orçamento até segunda, 02/11/2026" in e.corpo
    assert "R$ 4.500,00" in e.corpo
    for sensivel in ("123.456.789-09", "12345678-9", "0001-90", "98765-4321"):
        assert sensivel not in e.corpo
    assert "[número]" in e.corpo
    assert "Gerente de Projetos" not in e.corpo        # assinatura
    assert "me manda o orçamento" not in e.corpo       # histórico citado


def test_html_vira_texto_sem_citacao():
    coleta, _ = rodar()
    corpo = por_assunto(coleta)["Reunião de amanhã"].corpo
    assert "podemos mover a reunião para as 15h?" in corpo
    assert "Confirma até hoje & me avisa." in corpo
    for lixo in ("alert", "color", "Mensagem antiga", "aninhada", "Em ter."):
        assert lixo not in corpo


def test_enviado_por_voce_e_spam():
    msg = email.message_from_string("From: voce@example.org\nSubject: x\n\ncorpo", policy=policy.default)
    assert motivo_descarte(msg, "voce@example.org", PROPRIO, {}, False) == "enviado por você"
    msg = email.message_from_string("From: a@example.com\nX-Spam-Flag: YES\n\ncorpo", policy=policy.default)
    assert motivo_descarte(msg, "a@example.com", PROPRIO, {}, False) == "spam"


@pytest.mark.parametrize("endereco, padroes, esperado", [
    ("x@mail.freela.example.com", ["@freela.example.com"], True),
    ("x@freela.example.com", ["@FREELA.example.com"], True),
    ("x@notfreela.example.com", ["@freela.example.com"], False),
    ("maria@example.com", ["maria@example.com"], True),
    ("maria2@example.com", ["maria@example.com"], False),
])
def test_corresponde(endereco, padroes, esperado):
    assert corresponde(endereco, padroes) is esperado


def test_frase_comecando_com_em_nao_corta_o_texto():
    texto = "Em anexo segue o contrato.\nEle escreveu: confere tudo antes."
    assert limpar_corpo(texto, 1500) == texto


def test_trunca_no_limite():
    corpo = limpar_corpo("palavra " * 100, 50)
    assert corpo.endswith("…") and len(corpo) <= 51


def test_mascara_preserva_datas_horas_e_valores():
    texto = "Dia 31/10/2026 às 10:30, valor R$ 1.234,56, pedido 4521."
    assert mascarar_numeros(texto) == texto


@pytest.mark.parametrize("texto", ["02-Nov-2026 08:05:09 -0300", " 2-Nov-2026 08:05:09 -0300"])
def test_ler_internaldate(texto):
    # O IMAP pode mandar o dia com espaço à esquerda em vez de zero.
    assert ler_internaldate(texto) == datetime(2026, 11, 2, 8, 5, 9, tzinfo=BRT)


def test_coletar_reporta_erros_sem_parar(monkeypatch):
    monkeypatch.delenv("EMAIL_TESTE", raising=False)
    cfg = {"contas": [CONTA, {"nome": "trabalho", "tipo": "outlook_oauth"}],
           "filtros": {}, "privacidade": {"max_caracteres_corpo": 1500}}
    coleta = coletar(cfg, INICIO, FIM)
    assert len(coleta.erros) == 2
    assert "EMAIL_TESTE" in coleta.erros[0]
    assert "ainda não implementado" in coleta.erros[1]


def test_falha_de_conexao_vira_erro(monkeypatch):
    monkeypatch.setenv("EMAIL_TESTE", PROPRIO)
    monkeypatch.setenv("SENHA_TESTE", "x")

    def recusar(*a, **k):
        raise OSError("conexão recusada")

    monkeypatch.setattr(email_reader.imaplib, "IMAP4_SSL", recusar)
    cfg = {"contas": [CONTA], "filtros": {}, "privacidade": {"max_caracteres_corpo": 1500}}
    coleta = coletar(cfg, INICIO, FIM)
    assert coleta.erros == ["[pessoal] conexão recusada"]
