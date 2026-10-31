"""Orquestra a análise dos e-mails pela IA configurada.

Os e-mails vão em lotes pequenos. Se um lote falhar ou vier incompleto, ele é tentado
de novo, depois vai para a reserva (llm.fallback) e por fim para as regras, que nunca
falham. Depois do prazo (fim da janela de análise), o que sobrou vai direto para as regras.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from src.config import perfil
from src.email_reader import Email, corresponde
from src.llm import criar_provedor
from src.llm.base import Analise, Contexto, ErroIA, Provedor
from src.llm.regras import ProvedorRegras

log = logging.getLogger("assistente.ia")
TENTATIVAS = 2


@dataclass
class ResultadoAnalise:
    analises: list[Analise] = field(default_factory=list)   # na ordem dos e-mails
    tokens_entrada: int = 0
    tokens_saida: int = 0
    segundos: float = 0.0
    por_provedor: dict[str, int] = field(default_factory=dict)
    erros: list[str] = field(default_factory=list)


def montar_contexto(cfg: dict, hoje: date, quadro: list[tuple[int, str]] | None = None) -> Contexto:
    return Contexto(hoje=hoje, perfil=perfil(cfg), quadro=list(quadro or []))


def analisar(emails: list[Email], cfg: dict, contexto: Contexto, prazo_limite: datetime | None = None,
             principal: Provedor | None = None, reserva: Provedor | None = None,
             relogio=datetime.now) -> ResultadoAnalise:
    llm = cfg["llm"]
    principal = principal or criar_provedor(llm["provider"], cfg)
    if reserva is None and llm.get("fallback", "none") not in ("none", principal.nome):
        reserva = criar_provedor(llm["fallback"], cfg)
    regras = ProvedorRegras(cfg)
    feitas: dict[str, Analise] = {}
    resultado = ResultadoAnalise()
    inicio = time.monotonic()

    pendentes = list(emails)
    dominios_locais = [f"@{d.lstrip('@')}" for d in llm.get("local_only_domains") or []]
    if principal.nome == "claude" and dominios_locais:
        # Esses e-mails nunca saem do servidor: vão para o Ollama (se for a reserva) ou para as regras.
        protegidos = [e for e in pendentes if corresponde(e.remetente, dominios_locais)]
        pendentes = [e for e in pendentes if e not in protegidos]
        local = reserva if reserva and reserva.nome == "ollama" else regras
        sobra = _processar(local, protegidos, contexto, feitas, resultado, "domínios locais")
        _processar(regras, sobra, contexto, feitas, resultado, "domínios locais")

    tamanho = llm["lote_emails"]
    lotes = [pendentes[i:i + tamanho] for i in range(0, len(pendentes), tamanho)]
    for numero, lote in enumerate(lotes, start=1):
        if prazo_limite and relogio(prazo_limite.tzinfo) >= prazo_limite:
            restantes = [e for l in lotes[numero - 1:] for e in l]
            resultado.erros.append(f"prazo da análise esgotado; {len(restantes)} e-mail(s) foram pelas regras")
            _processar(regras, restantes, contexto, feitas, resultado, "prazo esgotado")
            break
        rotulo = f"lote {numero}/{len(lotes)}"
        faltando = _processar(principal, lote, contexto, feitas, resultado, rotulo)
        if faltando and reserva:
            faltando = _processar(reserva, faltando, contexto, feitas, resultado, f"{rotulo} (reserva)")
        if faltando:
            _processar(regras, faltando, contexto, feitas, resultado, f"{rotulo} (regras)")

    _ajustar(feitas, emails, cfg)
    if llm.get("rascunhos") == "lote":
        _rascunhar(emails, feitas, principal, contexto, resultado)

    resultado.analises = [feitas[e.message_id] for e in emails if e.message_id in feitas]
    resultado.segundos = time.monotonic() - inicio
    log.info("análise concluída: %d e-mail(s) em %.1f s, tokens %d entrada / %d saída, por provedor %s",
             len(resultado.analises), resultado.segundos, resultado.tokens_entrada, resultado.tokens_saida,
             resultado.por_provedor)
    return resultado


def _processar(provedor: Provedor, emails: list[Email], contexto: Contexto, feitas: dict[str, Analise],
               resultado: ResultadoAnalise, rotulo: str) -> list[Email]:
    """Analisa e devolve os e-mails que continuaram sem análise válida."""
    pendentes = list(emails)
    tentativas = 1 if provedor.nome == "regras" else TENTATIVAS
    for tentativa in range(1, tentativas + 1):
        if not pendentes:
            break
        inicio = time.monotonic()
        try:
            lote = provedor.analisar(pendentes, contexto)
        except ErroIA as e:
            log.warning("%s via %s, tentativa %d: %s", rotulo, provedor.nome, tentativa, e)
            resultado.erros.append(f"{rotulo} via {provedor.nome}: {e}")
            continue
        resultado.tokens_entrada += lote.tokens_entrada
        resultado.tokens_saida += lote.tokens_saida
        for analise in lote.analises:
            feitas[analise.message_id] = analise
            resultado.por_provedor[provedor.nome] = resultado.por_provedor.get(provedor.nome, 0) + 1
        log.info("%s via %s: %d de %d e-mail(s) em %.1f s, tokens %d entrada / %d saída", rotulo, provedor.nome,
                 len(lote.analises), len(pendentes), time.monotonic() - inicio, lote.tokens_entrada,
                 lote.tokens_saida)
        pendentes = [e for e in pendentes if e.message_id not in feitas]
    return pendentes


def _ajustar(feitas: dict[str, Analise], emails: list[Email], cfg: dict) -> None:
    """Regras do config valem mais que a opinião da IA."""
    vip = (cfg.get("filtros") or {}).get("vip", [])
    fontes = (cfg.get("oportunidades") or {}).get("fontes", [])
    for e in emails:
        a = feitas.get(e.message_id)
        if a is None:
            continue
        if corresponde(e.remetente, vip):
            a.categoria, a.prioridade = "tarefa", "alta"
        elif corresponde(e.remetente, fontes):
            # Site de projetos: o que a IA viu como tarefa vira oportunidade; propaganda segue informativo.
            if a.categoria == "tarefa":
                a.categoria = "oportunidade"
        elif a.categoria == "oportunidade":
            # Proposta vinda de uma pessoa (não de um site de projetos) precisa de resposta.
            a.categoria, a.tipo = "tarefa", "responder"
        if a.categoria != "oportunidade":
            a.aderencia = 0


def _rascunhar(emails: list[Email], feitas: dict[str, Analise], provedor: Provedor, contexto: Contexto,
               resultado: ResultadoAnalise) -> None:
    for e in emails:
        a = feitas.get(e.message_id)
        if not a or a.categoria != "tarefa" or a.tipo != "responder":
            continue
        try:
            a.rascunho, entrada, saida = provedor.rascunhar(e, contexto)
        except ErroIA as erro:
            resultado.erros.append(f"rascunho de {e.assunto!r}: {erro}")
            continue
        resultado.tokens_entrada += entrada
        resultado.tokens_saida += saida
