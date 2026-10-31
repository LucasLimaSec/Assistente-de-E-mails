import io
import json
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from src.email_reader import Email
from src.llm import base, ollama as modulo_ollama
from src.llm.base import ESQUEMA_LOTE, Contexto, ErroIA, ler_json, validar_lote
from src.llm.claude import ProvedorClaude
from src.llm.ollama import ProvedorOllama
from src.llm.regras import ProvedorRegras

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
CONTEXTO = Contexto(hoje=date(2026, 10, 30), perfil="Pentest web, APIs, Python", quadro=[(7, "Proposta empresa Y")])


def novo_email(n, remetente="maria@example.com", nome="Maria Souza", assunto="Orçamento", corpo="Pode aprovar?"):
    return Email(conta="pessoal", uid=str(n), message_id=f"<m{n}@example.com>", conversa=f"<m{n}@example.com>",
                 remetente_nome=nome, remetente=remetente, assunto=assunto,
                 data=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc), corpo=corpo)


def item(i, **campos):
    return {"id": i, "categoria": "tarefa", "tipo": "responder", "prioridade": "alta", "titulo": "Aprovar orçamento",
            "prazo": "", "aderencia": 0, "tarefa_existente": 0, **campos}


# --- validação do JSON -------------------------------------------------------------------------

def test_validar_lote_normaliza_e_limita():
    emails = [novo_email(1), novo_email(2)]
    dados = {"itens": [
        item(1, prioridade="Média", prazo="2026-11-02", tarefa_existente=7),
        item(2, categoria="oportunidade", aderencia=15, prazo="amanhã", tarefa_existente=99),
    ]}
    a1, a2 = validar_lote(dados, emails, {7}, "teste")
    assert (a1.message_id, a1.prioridade, a1.prazo, a1.tarefa_existente) == ("<m1@example.com>", "media", "2026-11-02", 7)
    assert (a2.aderencia, a2.prazo, a2.tarefa_existente) == (10, "", 0)


def test_validar_lote_descarta_itens_invalidos():
    emails = [novo_email(1), novo_email(2), novo_email(3)]
    dados = {"itens": [item(1, categoria="urgente"), item(2, titulo="  "), item(9), "lixo", item(3)]}
    assert [a.message_id for a in validar_lote(dados, emails, set(), "teste")] == ["<m3@example.com>"]


def test_validar_lote_exige_lista_itens():
    with pytest.raises(ErroIA):
        validar_lote({"resultado": []}, [novo_email(1)], set(), "teste")


def test_ler_json_aceita_cerca_e_recusa_texto():
    assert ler_json('```json\n{"itens": []}\n```') == {"itens": []}
    with pytest.raises(ErroIA):
        ler_json("Claro! Aqui está a análise.")


# --- regras ------------------------------------------------------------------------------------

def test_regras():
    cfg = {"filtros": {"vip": ["@banco.example.com"]}, "oportunidades": {"fontes": ["@freela.example.com"]}}
    emails = [
        novo_email(1, assunto="Re: RES: Orçamento"),
        novo_email(2, remetente="no-reply@banco.example.com", nome="Banco", assunto="Alerta de acesso"),
        novo_email(3, remetente="no-reply@freela.example.com", assunto="Novo projeto: pentest em API Python",
                   corpo="Teste de invasão em API REST"),
    ]
    a1, a2, a3 = ProvedorRegras(cfg).analisar(emails, CONTEXTO).analises
    assert (a1.categoria, a1.tipo, a1.prioridade, a1.titulo) == ("tarefa", "responder", "media", "Maria: Orçamento")
    assert (a2.tipo, a2.prioridade, a2.titulo) == ("realizar", "alta", "Alerta de acesso")
    assert a3.categoria == "oportunidade" and a3.aderencia == 4   # pentest e python ("APIs" ≠ "API")


# --- Ollama (HTTP simulado) --------------------------------------------------------------------

class RespostaFalsa(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def ollama():
    return ProvedorOllama({"url": "http://ollama:11434/", "model": "qwen3.5:4b", "num_ctx": 8192, "think": False},
                          PROMPTS)


def simular_ollama(monkeypatch, respostas):
    pedidos = []

    def urlopen(pedido, timeout):
        pedidos.append((pedido.full_url, json.loads(pedido.data) if pedido.data else None))
        resposta = respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return RespostaFalsa(json.dumps(resposta).encode())

    monkeypatch.setattr(modulo_ollama.urllib.request, "urlopen", urlopen)
    return pedidos


def test_ollama_analisa_com_schema(monkeypatch, ollama):
    conteudo = json.dumps({"itens": [item(1)]})
    pedidos = simular_ollama(monkeypatch, [{"message": {"content": conteudo}, "prompt_eval_count": 900,
                                            "eval_count": 60}])
    lote = ollama.analisar([novo_email(1)], CONTEXTO)
    url, corpo = pedidos[0]
    assert url == "http://ollama:11434/api/chat"
    assert corpo["format"] == ESQUEMA_LOTE
    assert corpo["think"] is False and corpo["stream"] is False
    assert corpo["options"]["num_ctx"] == 8192
    assert corpo["options"]["num_predict"] == 200 + 150   # teto de saída para 1 e-mail
    sistema, usuario = corpo["messages"][0]["content"], corpo["messages"][1]["content"]
    assert "Pentest web, APIs, Python" in sistema and "#7 Proposta empresa Y" in sistema
    assert '<email id="1">' in usuario and "Pode aprovar?" in usuario
    assert (lote.tokens_entrada, lote.tokens_saida, lote.analises[0].provedor) == (900, 60, "ollama")


def test_ollama_erros_viram_erro_ia(monkeypatch, ollama):
    erro_http = urllib.error.HTTPError("u", 500, "erro", {}, io.BytesIO(b"model failed to load"))
    simular_ollama(monkeypatch, [urllib.error.URLError("recusado"), erro_http,
                                 {"message": {"content": "não é json"}}])
    for _ in range(3):
        with pytest.raises(ErroIA):
            ollama.analisar([novo_email(1)], CONTEXTO)


def test_ollama_resposta_cortada_vira_erro(monkeypatch, ollama):
    simular_ollama(monkeypatch, [{"message": {"content": '{"itens": [{"id": 1, '}, "done_reason": "length"}])
    with pytest.raises(ErroIA, match="loop"):
        ollama.analisar([novo_email(1)], CONTEXTO)


def test_ollama_disponivel(monkeypatch, ollama):
    simular_ollama(monkeypatch, [{"models": [{"name": "qwen3.5:2b"}]}, {"models": [{"name": "qwen3.5:4b"}]}])
    ok, detalhe = ollama.disponivel()
    assert not ok and "ollama pull qwen3.5:4b" in detalhe
    assert ollama.disponivel()[0]


def test_ollama_rascunho_sem_schema(monkeypatch, ollama):
    pedidos = simular_ollama(monkeypatch, [{"message": {"content": "Oi Maria, aprovado. [confirmar valor]"}}])
    texto, _, _ = ollama.rascunhar(novo_email(1), CONTEXTO)
    assert texto.startswith("Oi Maria") and "format" not in pedidos[0][1]


# --- Claude (cliente simulado) -----------------------------------------------------------------

class ClienteFalso:
    def __init__(self, resposta=None, erro=None):
        self.chamadas = []
        self.resposta, self.erro = resposta, erro
        self.messages = self

    def create(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro:
            raise self.erro
        return self.resposta


def resposta_claude(texto, stop_reason="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=texto)],
                           stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=1200, output_tokens=90))


def claude(cliente):
    return ProvedorClaude({"model": "claude-haiku-5-5", "effort": "low"}, PROMPTS, cliente=cliente)


def test_claude_usa_saida_estruturada():
    cliente = ClienteFalso(resposta_claude(json.dumps({"itens": [item(1)]})))
    lote = claude(cliente).analisar([novo_email(1)], CONTEXTO)
    chamada = cliente.chamadas[0]
    assert chamada["model"] == "claude-haiku-5-5"
    assert chamada["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA_LOTE}}
    assert (lote.tokens_entrada, lote.tokens_saida, lote.analises[0].provedor) == (1200, 90, "claude")


@pytest.mark.parametrize("cliente", [
    ClienteFalso(resposta_claude("{}", stop_reason="refusal")),
    ClienteFalso(resposta_claude('{"itens": [', stop_reason="max_tokens")),
    ClienteFalso(erro=anthropic.APIConnectionError(request=None)),
])
def test_claude_falhas_viram_erro_ia(cliente):
    with pytest.raises(ErroIA):
        claude(cliente).analisar([novo_email(1)], CONTEXTO)


def test_claude_indisponivel_sem_chave(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not claude(ClienteFalso()).disponivel()[0]
