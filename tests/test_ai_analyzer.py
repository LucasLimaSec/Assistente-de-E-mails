from datetime import date, datetime, timedelta, timezone

from src.ai_analyzer import analisar
from src.llm.base import Analise, Contexto, ErroIA, Provedor, ResultadoLote
from tests.test_llm import novo_email

CONTEXTO = Contexto(hoje=date(2026, 10, 30))


def cfg(**llm):
    return {
        "_pasta": None,
        "filtros": {"vip": ["@banco.example.com"]},
        "oportunidades": {"fontes": ["@freela.example.com"]},
        "llm": {"provider": "falso", "fallback": "none", "lote_emails": 2, "rascunhos": "sob_demanda", **llm},
    }


class ProvedorFalso(Provedor):
    """Responde conforme um roteiro: 'ok', 'erro' ou 'metade' (só o primeiro e-mail de cada chamada)."""

    def __init__(self, nome="falso", roteiro=None, categoria="tarefa"):
        self.nome = nome
        self.roteiro = list(roteiro or [])
        self.categoria = categoria
        self.lotes = []

    def analisar(self, emails, contexto):
        self.lotes.append([e.uid for e in emails])
        passo = self.roteiro.pop(0) if self.roteiro else "ok"
        if passo == "erro":
            raise ErroIA("simulado")
        alvo = emails[:1] if passo == "metade" else emails
        return ResultadoLote([Analise(e.message_id, self.categoria, "responder", "baixa", e.assunto, provedor=self.nome)
                              for e in alvo], tokens_entrada=100, tokens_saida=10)

    def rascunhar(self, email, contexto):
        return f"Rascunho para {email.assunto}", 50, 20


def test_divide_em_lotes_e_soma_tokens():
    emails = [novo_email(i) for i in range(5)]
    principal = ProvedorFalso()
    resultado = analisar(emails, cfg(), CONTEXTO, principal=principal)
    assert principal.lotes == [["0", "1"], ["2", "3"], ["4"]]
    assert [a.message_id for a in resultado.analises] == [e.message_id for e in emails]
    assert (resultado.tokens_entrada, resultado.por_provedor) == (300, {"falso": 5})


def test_tenta_de_novo_e_depois_usa_reserva_e_regras():
    emails = [novo_email(1), novo_email(2)]
    principal = ProvedorFalso(roteiro=["erro", "metade"])     # 2ª tentativa só devolve o 1º e-mail
    reserva = ProvedorFalso(nome="reserva", roteiro=["erro", "erro"])
    resultado = analisar(emails, cfg(), CONTEXTO, principal=principal, reserva=reserva)
    assert principal.lotes == [["1", "2"], ["1", "2"]]
    assert reserva.lotes == [["2"], ["2"]]                    # a reserva também tenta duas vezes
    assert [a.provedor for a in resultado.analises] == ["falso", "regras"]
    assert len(resultado.erros) == 3


def test_prazo_esgotado_manda_o_resto_para_as_regras():
    emails = [novo_email(i) for i in range(4)]
    principal = ProvedorFalso()
    limite = datetime(2026, 10, 30, 7, 55, tzinfo=timezone.utc)
    horarios = iter([limite - timedelta(minutes=5), limite])   # 1º lote antes do prazo, 2º já depois
    resultado = analisar(emails, cfg(), CONTEXTO, principal=principal, prazo_limite=limite,
                         relogio=lambda tz: next(horarios))
    assert principal.lotes == [["0", "1"]]
    assert [a.provedor for a in resultado.analises] == ["falso", "falso", "regras", "regras"]
    assert "prazo" in resultado.erros[0]


def test_config_vale_mais_que_a_ia():
    emails = [
        novo_email(1, remetente="no-reply@banco.example.com"),     # vip
        novo_email(2, remetente="no-reply@freela.example.com"),    # fonte de oportunidades
        novo_email(3),                                              # pessoa comum
    ]
    resultado = analisar(emails, cfg(lote_emails=6), CONTEXTO, principal=ProvedorFalso(categoria="oportunidade"))
    vip, fonte, pessoa = resultado.analises
    assert (vip.categoria, vip.prioridade) == ("tarefa", "alta")
    assert fonte.categoria == "oportunidade"
    assert (pessoa.categoria, pessoa.tipo) == ("tarefa", "responder")


def test_informativo_de_site_de_projetos_continua_informativo():
    emails = [novo_email(1, remetente="noreply@freela.example.com")]
    resultado = analisar(emails, cfg(), CONTEXTO, principal=ProvedorFalso(categoria="informativo"))
    assert resultado.analises[0].categoria == "informativo"


def test_dominios_locais_nunca_vao_para_a_api():
    emails = [novo_email(1, remetente="chefe@empresa.example.com"), novo_email(2)]
    principal = ProvedorFalso(nome="claude")
    resultado = analisar(emails, cfg(local_only_domains=["empresa.example.com"]), CONTEXTO, principal=principal)
    assert principal.lotes == [["2"]]
    assert [a.provedor for a in resultado.analises] == ["regras", "claude"]


def test_rascunhos_em_lote():
    resultado = analisar([novo_email(1, assunto="Orçamento")], cfg(rascunhos="lote"), CONTEXTO,
                         principal=ProvedorFalso())
    assert resultado.analises[0].rascunho == "Rascunho para Orçamento"
    assert resultado.tokens_saida == 30
