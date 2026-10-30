"""Carrega e valida o config.yaml."""

import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
TIPOS_CONTA = {"imap", "outlook_oauth"}
PROVEDORES_LLM = {"claude", "ollama"}
FALLBACKS_LLM = {"none", "ollama"}
_HORARIO = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def carregar_config(caminho: str | Path) -> dict:
    caminho = Path(caminho).resolve()
    with open(caminho, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg["_pasta"] = caminho.parent
    return cfg


def caminho_banco(cfg: dict) -> Path:
    banco = Path(cfg["geral"]["banco"])
    return banco if banco.is_absolute() else cfg["_pasta"] / banco


def validar_config(cfg: dict) -> list[str]:
    """Retorna a lista de erros encontrados (vazia se o config está ok)."""
    erros = []

    geral = cfg.get("geral") or {}
    try:
        ZoneInfo(str(geral.get("fuso_horario")))
    except (ZoneInfoNotFoundError, ValueError):
        erros.append(f"geral.fuso_horario inválido: {geral.get('fuso_horario')!r}")
    if not _HORARIO.match(str(geral.get("horario_relatorio", ""))):
        erros.append("geral.horario_relatorio deve estar no formato HH:MM")
    dias = geral.get("dias_uteis") or []
    if not dias or any(d not in DIAS_SEMANA for d in dias):
        erros.append(f"geral.dias_uteis deve listar dias entre {DIAS_SEMANA}")
    if not geral.get("banco"):
        erros.append("geral.banco não definido")

    contas = cfg.get("contas") or []
    if not contas:
        erros.append("nenhuma conta de e-mail em 'contas'")
    nomes = [c.get("nome") for c in contas]
    if len(set(nomes)) != len(nomes):
        erros.append("contas: nomes repetidos")
    for i, conta in enumerate(contas):
        rotulo = f"contas[{conta.get('nome') or i}]"
        if not conta.get("nome"):
            erros.append(f"{rotulo}: falta 'nome'")
        if not conta.get("endereco_env"):
            erros.append(f"{rotulo}: falta 'endereco_env'")
        if conta.get("tipo") not in TIPOS_CONTA:
            erros.append(f"{rotulo}: tipo deve ser um de {sorted(TIPOS_CONTA)}")
        if conta.get("tipo") == "imap":
            for campo in ("servidor", "porta", "senha_env"):
                if not conta.get(campo):
                    erros.append(f"{rotulo}: falta '{campo}'")

    filtros = cfg.get("filtros") or {}
    for lista in ("excluir_remetentes", "incluir_sempre", "vip"):
        if not isinstance(filtros.get(lista, []), list):
            erros.append(f"filtros.{lista} deve ser uma lista")

    kanban = cfg.get("kanban") or {}
    for campo in ("dias_para_atraso", "max_itens_por_coluna"):
        valor = kanban.get(campo)
        if not isinstance(valor, int) or valor < 1:
            erros.append(f"kanban.{campo} deve ser um inteiro positivo")

    semanal = cfg.get("resumo_semanal") or {}
    if semanal.get("enabled"):
        if semanal.get("dia") not in DIAS_SEMANA:
            erros.append(f"resumo_semanal.dia deve ser um de {DIAS_SEMANA}")
        if not _HORARIO.match(str(semanal.get("horario", ""))):
            erros.append("resumo_semanal.horario deve estar no formato HH:MM")

    llm = cfg.get("llm") or {}
    if llm.get("provider") not in PROVEDORES_LLM:
        erros.append(f"llm.provider deve ser um de {sorted(PROVEDORES_LLM)}")
    if llm.get("fallback", "none") not in FALLBACKS_LLM:
        erros.append(f"llm.fallback deve ser um de {sorted(FALLBACKS_LLM)}")
    if llm.get("provider") == "claude" and not llm.get("model"):
        erros.append("llm.model não definido")

    return erros
