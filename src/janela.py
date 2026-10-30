"""Janela de leitura do relatório: desde o relatório do último dia útil."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.config import DIAS_SEMANA


def inicio_janela(agora: datetime, cfg: dict) -> datetime:
    """Horário do relatório no último dia útil antes de hoje.

    Na segunda, com sábado como último dia útil, a janela cobre sábado e domingo.
    """
    fuso = ZoneInfo(cfg["geral"]["fuso_horario"])
    hora, minuto = map(int, cfg["geral"]["horario_relatorio"].split(":"))
    # DIAS_SEMANA segue a ordem de date.weekday(): seg = 0 ... dom = 6.
    dias_uteis = {DIAS_SEMANA.index(d) for d in cfg["geral"]["dias_uteis"]}

    dia = agora.astimezone(fuso).date() - timedelta(days=1)
    while dia.weekday() not in dias_uteis:
        dia -= timedelta(days=1)
    return datetime.combine(dia, time(hora, minuto), tzinfo=fuso)
