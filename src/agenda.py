"""Google Agenda pelo endereço secreto iCal (somente leitura, sem OAuth).

Os eventos de ontem entram no relatório como "feito"; os de hoje, como agenda do dia.
Eventos repetidos (RRULE) são expandidos pelo recurring-ical-events.
"""

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

TIMEOUT = 20
LIMITE_BYTES = 5_000_000
VARIAVEL_URL = "GOOGLE_AGENDA_ICAL_URL"


class ErroAgenda(Exception):
    pass


@dataclass
class Evento:
    titulo: str
    inicio: datetime
    dia_inteiro: bool = False


def ativa(cfg: dict) -> bool:
    return bool((cfg.get("agenda") or {}).get("enabled")) and bool(os.environ.get(VARIAVEL_URL))


def baixar(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resposta:
            dados = resposta.read(LIMITE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Não repete a URL na mensagem: ela é secreta.
        raise ErroAgenda(f"não foi possível baixar a agenda ({type(e).__name__})") from e
    if len(dados) > LIMITE_BYTES:
        raise ErroAgenda("agenda maior que 5 MB")
    return dados


def eventos(dados: bytes, inicio: datetime, fim: datetime, fuso: ZoneInfo) -> list[Evento]:
    """Eventos que começam entre `inicio` (inclusive) e `fim` (exclusive); de dia inteiro, pela data."""
    try:
        calendario = icalendar.Calendar.from_ical(dados)
        # Uma margem de um dia nas pontas pega os eventos de dia inteiro; o filtro exato vem abaixo.
        brutos = recurring_ical_events.of(calendario).between(inicio - timedelta(days=1), fim + timedelta(days=1))
    except Exception as e:  # iCal malformado: a biblioteca lança erros variados
        raise ErroAgenda(f"agenda em formato inválido ({type(e).__name__})") from e

    lista = []
    for ev in brutos:
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        comeco = ev.decoded("DTSTART")
        titulo = " ".join(str(ev.get("SUMMARY", "")).split()) or "(sem título)"
        if isinstance(comeco, datetime):
            comeco = comeco.replace(tzinfo=fuso) if comeco.tzinfo is None else comeco.astimezone(fuso)
            if inicio <= comeco < fim:
                lista.append(Evento(titulo, comeco))
        elif isinstance(comeco, date):
            # Dia inteiro: conta se o dia cai dentro da janela (pelas datas locais).
            if inicio.astimezone(fuso).date() <= comeco < _dia_final(fim, fuso):
                lista.append(Evento(titulo, datetime.combine(comeco, time(0), fuso), dia_inteiro=True))
    return sorted(lista, key=lambda e: (e.inicio, not e.dia_inteiro, e.titulo))


def _dia_final(fim: datetime, fuso: ZoneInfo) -> date:
    """Primeiro dia que fica fora da janela (fim exclusivo)."""
    local = fim.astimezone(fuso)
    return local.date() if local.time() == time(0) else local.date() + timedelta(days=1)


def feitos_e_hoje(cfg: dict, desde: datetime, agora: datetime) -> tuple[list[Evento], list[Evento]]:
    """(eventos desde o último relatório até ontem, eventos de hoje)."""
    fuso = ZoneInfo(cfg["geral"]["fuso_horario"])
    dados = baixar(os.environ[VARIAVEL_URL])
    hoje = datetime.combine(agora.astimezone(fuso).date(), time(0), fuso)
    amanha = hoje + timedelta(days=1)
    # Feitos: do último relatório até o fim de ontem (eventos de hoje cedo ficam em "hoje").
    feitos = eventos(dados, desde, hoje, fuso)
    return feitos, eventos(dados, hoje, amanha, fuso)
