"""Provedor sem IA: classifica por regras fixas. Sempre disponível, usado como reserva."""

import re

from src.email_reader import Email, assunto_sem_prefixos, corresponde, e_automatico
from src.llm.base import MAX_TITULO, Analise, Contexto, Provedor, ResultadoLote, sem_acento

MIN_LETRAS_PALAVRA = 3  # "API" e "web" contam


class ProvedorRegras(Provedor):
    nome = "regras"

    def __init__(self, cfg: dict):
        self.vip = (cfg.get("filtros") or {}).get("vip", [])
        self.fontes = (cfg.get("oportunidades") or {}).get("fontes", [])

    def analisar(self, emails: list[Email], contexto: Contexto) -> ResultadoLote:
        termos_perfil = palavras(contexto.perfil)
        return ResultadoLote([self._classificar(e, termos_perfil) for e in emails])

    def _classificar(self, e: Email, termos_perfil: set[str]) -> Analise:
        assunto = assunto_sem_prefixos(e.assunto)
        if corresponde(e.remetente, self.fontes):
            # Cada palavra do perfil encontrada no projeto soma 2 pontos.
            comuns = termos_perfil & palavras(f"{e.assunto} {e.corpo}")
            return Analise(e.message_id, "oportunidade", "realizar", "baixa", assunto[:MAX_TITULO],
                           aderencia=min(10, 2 * len(comuns)), provedor=self.nome)

        automatico = e_automatico(e.remetente)
        quem = (e.remetente_nome or e.remetente).split()[0] if not automatico else ""
        titulo = f"{quem}: {assunto}" if quem else assunto
        return Analise(
            e.message_id,
            "tarefa",
            "realizar" if automatico else "responder",
            "alta" if corresponde(e.remetente, self.vip) else "media",
            titulo[:MAX_TITULO],
            provedor=self.nome,
        )


def palavras(texto: str) -> set[str]:
    return {p for p in re.findall(r"\w+", sem_acento(texto).lower()) if len(p) >= MIN_LETRAS_PALAVRA}
