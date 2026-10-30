import copy
from pathlib import Path

import pytest

from src.config import caminho_banco, carregar_config, validar_config

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg():
    return carregar_config(RAIZ / "config.yaml")


def test_config_do_projeto_e_valido(cfg):
    assert validar_config(cfg) == []


def test_banco_relativo_a_pasta_do_config(cfg):
    assert caminho_banco(cfg) == RAIZ / "data" / "assistente.db"


@pytest.mark.parametrize("alterar, trecho_do_erro", [
    (lambda c: c["geral"].update(fuso_horario="Marte/Olympus"), "fuso_horario"),
    (lambda c: c["geral"].update(horario_relatorio="7h"), "horario_relatorio"),
    (lambda c: c["geral"].update(dias_uteis=["segunda"]), "dias_uteis"),
    (lambda c: c.update(contas=[]), "nenhuma conta"),
    (lambda c: c["contas"].append(copy.deepcopy(c["contas"][0])), "nomes repetidos"),
    (lambda c: c["contas"][0].update(tipo="pop3"), "tipo"),
    (lambda c: c["contas"][0].pop("senha_env"), "senha_env"),
    (lambda c: c["contas"][0].pop("endereco_env"), "endereco_env"),
    (lambda c: c["kanban"].update(dias_para_atraso=0), "dias_para_atraso"),
    (lambda c: c["resumo_semanal"].update(dia="sabado"), "resumo_semanal.dia"),
    (lambda c: c["llm"].update(provider="gemini"), "llm.provider"),
])
def test_detecta_erros(cfg, alterar, trecho_do_erro):
    alterar(cfg)
    erros = validar_config(cfg)
    assert any(trecho_do_erro in e for e in erros), erros
