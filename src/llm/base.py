"""Interface única dos provedores de IA: analisar(emails, contexto) -> ResultadoLote.

ProvedorLLM concentra o que é comum às IAs (prompts, schema do JSON e validação);
cada IA só implementa como gerar JSON e texto.
"""

import json
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from string import Template

from src.email_reader import Email

CATEGORIAS = ("tarefa", "oportunidade", "informativo")
TIPOS = ("responder", "realizar")
PRIORIDADES = ("alta", "media", "baixa")
MAX_TITULO = 80
# Teto de saída por chamada: cada item do JSON tem ~80 tokens. Evita que um modelo pequeno
# entre em loop gerando itens até estourar o contexto (minutos perdidos num servidor lento).
TOKENS_SAIDA_POR_EMAIL = 150
TOKENS_SAIDA_BASE = 200
TOKENS_SAIDA_RASCUNHO = 600
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

# Sem campos opcionais nem limites numéricos: o mesmo schema serve ao Ollama e à API do Claude.
ESQUEMA_LOTE = {
    "type": "object",
    "properties": {
        "itens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "categoria": {"type": "string", "enum": list(CATEGORIAS)},
                    "tipo": {"type": "string", "enum": list(TIPOS)},
                    "prioridade": {"type": "string", "enum": list(PRIORIDADES)},
                    "titulo": {"type": "string"},
                    "prazo": {"type": "string"},
                    "aderencia": {"type": "integer"},
                    "tarefa_existente": {"type": "integer"},
                },
                "required": ["id", "categoria", "tipo", "prioridade", "titulo", "prazo", "aderencia",
                             "tarefa_existente"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["itens"],
    "additionalProperties": False,
}


@dataclass
class Analise:
    message_id: str
    categoria: str
    tipo: str
    prioridade: str
    titulo: str
    prazo: str = ""            # AAAA-MM-DD ou ""
    aderencia: int = 0         # 0 a 10, só em oportunidades
    tarefa_existente: int = 0  # número de uma tarefa aberta do mesmo assunto, ou 0
    provedor: str = ""
    rascunho: str = ""


@dataclass
class ResultadoLote:
    analises: list[Analise] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_saida: int = 0


@dataclass
class Contexto:
    hoje: date
    perfil: str = ""
    quadro: list[tuple[int, str]] = field(default_factory=list)  # tarefas abertas: (número, título)


class ErroIA(Exception):
    """IA indisponível ou resposta fora do formato."""


class Provedor(ABC):
    nome = ""

    @abstractmethod
    def analisar(self, emails: list[Email], contexto: Contexto) -> ResultadoLote:
        ...

    def rascunhar(self, email: Email, contexto: Contexto) -> tuple[str, int, int]:
        """Rascunho de resposta: (texto, tokens de entrada, tokens de saída)."""
        raise ErroIA(f"o provedor {self.nome!r} não escreve rascunhos")

    def disponivel(self) -> tuple[bool, str]:
        return True, "pronto"


class ProvedorLLM(Provedor):
    def __init__(self, pasta_prompts: Path):
        self.prompt_analise = Template((pasta_prompts / "analise.md").read_text(encoding="utf-8"))
        self.prompt_rascunho = Template((pasta_prompts / "rascunho.md").read_text(encoding="utf-8"))

    @abstractmethod
    def _gerar_json(self, sistema: str, usuario: str, esquema: dict, max_saida: int) -> tuple[dict, int, int]:
        ...

    @abstractmethod
    def _gerar_texto(self, sistema: str, usuario: str, max_saida: int) -> tuple[str, int, int]:
        ...

    def analisar(self, emails: list[Email], contexto: Contexto) -> ResultadoLote:
        quadro = "\n".join(f"#{n} {titulo}" for n, titulo in contexto.quadro) or "(nenhuma)"
        sistema = self.prompt_analise.safe_substitute(
            hoje=f"{contexto.hoje:%Y-%m-%d} ({DIAS[contexto.hoje.weekday()]})",
            perfil=contexto.perfil or "(não informado)",
            quadro=quadro,
        )
        usuario = "\n\n".join(formatar_email(i, e) for i, e in enumerate(emails, start=1))
        max_saida = TOKENS_SAIDA_BASE + TOKENS_SAIDA_POR_EMAIL * len(emails)
        dados, entrada, saida = self._gerar_json(sistema, usuario, ESQUEMA_LOTE, max_saida)
        quadro_ids = {n for n, _ in contexto.quadro}
        return ResultadoLote(validar_lote(dados, emails, quadro_ids, self.nome), entrada, saida)

    def rascunhar(self, email: Email, contexto: Contexto) -> tuple[str, int, int]:
        sistema = self.prompt_rascunho.safe_substitute(perfil=contexto.perfil or "(não informado)")
        texto, entrada, saida = self._gerar_texto(sistema, formatar_email(1, email), TOKENS_SAIDA_RASCUNHO)
        if not texto.strip():
            raise ErroIA("rascunho vazio")
        return texto.strip(), entrada, saida


def formatar_email(numero: int, e: Email) -> str:
    remetente = f"{e.remetente_nome} <{e.remetente}>" if e.remetente_nome else e.remetente
    return (f'<email id="{numero}">\nDe: {remetente}\nAssunto: {e.assunto}\n'
            f"Data: {e.data:%Y-%m-%d %H:%M}\nTexto:\n{e.corpo or '(vazio)'}\n</email>")


def ler_json(texto: str) -> dict:
    """Aceita o JSON puro ou cercado por ```json ... ```, como alguns modelos devolvem."""
    texto = texto.strip()
    cerca = re.search(r"```(?:json)?\s*(.*?)```", texto, re.DOTALL)
    if cerca:
        texto = cerca.group(1)
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as e:
        raise ErroIA(f"resposta não é JSON válido ({e.msg})") from e
    if not isinstance(dados, dict):
        raise ErroIA("resposta JSON não é um objeto")
    return dados


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn")


def _opcao(valor, opcoes: tuple[str, ...]) -> str | None:
    valor = sem_acento(valor).strip().lower()
    return valor if valor in opcoes else None


def _inteiro(valor, minimo: int, maximo: int) -> int:
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return minimo


def validar_lote(dados: dict, emails: list[Email], quadro_ids: set[int], provedor: str) -> list[Analise]:
    """Converte o JSON da IA em análises; itens inválidos ficam de fora (o chamador trata os faltantes)."""
    itens = dados.get("itens")
    if not isinstance(itens, list):
        raise ErroIA("resposta sem a lista 'itens'")
    analises = {}
    for item in itens:
        if not isinstance(item, dict):
            continue
        numero = _inteiro(item.get("id"), 0, len(emails))
        categoria = _opcao(item.get("categoria", ""), CATEGORIAS)
        tipo = _opcao(item.get("tipo", ""), TIPOS)
        prioridade = _opcao(item.get("prioridade", ""), PRIORIDADES)
        titulo = " ".join(str(item.get("titulo", "")).split())[:MAX_TITULO]
        if not numero or not categoria or not tipo or not prioridade or not titulo:
            continue
        prazo = str(item.get("prazo", "")).strip()
        existente = _inteiro(item.get("tarefa_existente"), 0, 10**9)
        analises[numero] = Analise(
            message_id=emails[numero - 1].message_id,
            categoria=categoria,
            tipo=tipo,
            prioridade=prioridade,
            titulo=titulo,
            prazo=prazo if re.fullmatch(r"\d{4}-\d{2}-\d{2}", prazo) else "",
            aderencia=_inteiro(item.get("aderencia"), 0, 10) if categoria == "oportunidade" else 0,
            tarefa_existente=existente if existente in quadro_ids else 0,
            provedor=provedor,
        )
    return [analises[n] for n in sorted(analises)]
