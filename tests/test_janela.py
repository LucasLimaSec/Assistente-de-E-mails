from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.janela import inicio_janela

SP = ZoneInfo("America/Sao_Paulo")
SEG_A_SAB = ["seg", "ter", "qua", "qui", "sex", "sab"]


def cfg(dias=SEG_A_SAB):
    return {"geral": {"fuso_horario": "America/Sao_Paulo", "horario_relatorio": "08:00", "dias_uteis": dias}}


@pytest.mark.parametrize("agora, esperado", [
    (datetime(2026, 10, 30, 8, 0), datetime(2026, 10, 29, 8, 0)),   # sex: desde qui
    (datetime(2026, 11, 2, 8, 0), datetime(2026, 10, 31, 8, 0)),    # seg: desde sáb (cobre domingo)
    (datetime(2026, 11, 1, 15, 0), datetime(2026, 10, 31, 8, 0)),   # dom (manual): desde sáb
    (datetime(2026, 10, 30, 23, 59), datetime(2026, 10, 29, 8, 0)), # mesmo dia, mais tarde
])
def test_desde_o_relatorio_do_ultimo_dia_util(agora, esperado):
    assert inicio_janela(agora.replace(tzinfo=SP), cfg()) == esperado.replace(tzinfo=SP)


def test_segunda_a_sexta_pula_o_fim_de_semana():
    agora = datetime(2026, 11, 2, 8, 0, tzinfo=SP)
    assert inicio_janela(agora, cfg(["seg", "ter", "qua", "qui", "sex"])) == datetime(2026, 10, 30, 8, 0, tzinfo=SP)


def test_converte_para_o_fuso_do_config():
    # 02:00 UTC de sábado ainda é sexta 23:00 em São Paulo.
    agora = datetime(2026, 10, 31, 2, 0, tzinfo=ZoneInfo("UTC"))
    assert inicio_janela(agora, cfg()) == datetime(2026, 10, 29, 8, 0, tzinfo=SP)
