"""Provedor API: Claude Haiku 5.5 com saída estruturada (JSON garantido pelo schema)."""

import os
from pathlib import Path

import anthropic

from src.llm.base import ErroIA, ProvedorLLM, ler_json

TIMEOUT = 120
# O Haiku 5.5 pensa antes de responder (thinking adaptativo) e esse raciocínio conta no max_tokens,
# por isso a folga acima do teto de saída pedido pela base.
FOLGA_RACIOCINIO = 4000


class ProvedorClaude(ProvedorLLM):
    nome = "claude"

    def __init__(self, cfg_claude: dict, pasta_prompts: Path, cliente=None):
        super().__init__(pasta_prompts)
        self.modelo = cfg_claude["model"]
        self.effort = cfg_claude.get("effort", "low")
        self._cliente = cliente

    @property
    def cliente(self):
        # Criado só na primeira chamada: sem chave, os outros provedores seguem funcionando.
        if self._cliente is None:
            self._cliente = anthropic.Anthropic(timeout=TIMEOUT, max_retries=2)
        return self._cliente

    def _gerar_json(self, sistema: str, usuario: str, esquema: dict, max_saida: int) -> tuple[dict, int, int]:
        resposta = self._criar(sistema, usuario, max_saida + FOLGA_RACIOCINIO,
                               {"effort": self.effort, "format": {"type": "json_schema", "schema": esquema}})
        return ler_json(_texto(resposta)), resposta.usage.input_tokens, resposta.usage.output_tokens

    def _gerar_texto(self, sistema: str, usuario: str, max_saida: int) -> tuple[str, int, int]:
        resposta = self._criar(sistema, usuario, max_saida + FOLGA_RACIOCINIO, {"effort": self.effort})
        return _texto(resposta), resposta.usage.input_tokens, resposta.usage.output_tokens

    def _criar(self, sistema: str, usuario: str, max_tokens: int, output_config: dict):
        try:
            resposta = self.cliente.messages.create(
                model=self.modelo,
                max_tokens=max_tokens,
                system=sistema,
                messages=[{"role": "user", "content": usuario}],
                output_config=output_config,
            )
        except anthropic.AuthenticationError as e:
            raise ErroIA("chave da API recusada (401); confira ANTHROPIC_API_KEY") from e
        except anthropic.PermissionDeniedError as e:
            raise ErroIA(f"chave sem permissão para {self.modelo} (403)") from e
        except anthropic.NotFoundError as e:
            raise ErroIA(f"modelo {self.modelo!r} não encontrado") from e
        except anthropic.RateLimitError as e:
            raise ErroIA("limite de uso da API atingido (429)") from e
        except anthropic.APIStatusError as e:
            raise ErroIA(f"erro {e.status_code} da API do Claude") from e
        except anthropic.APIConnectionError as e:
            raise ErroIA("sem conexão com a API do Claude") from e
        except anthropic.AnthropicError as e:  # ex.: nenhuma credencial configurada
            raise ErroIA(f"API do Claude indisponível ({e})") from e

        if resposta.stop_reason == "refusal":
            raise ErroIA("o Claude recusou o pedido")
        if resposta.stop_reason == "max_tokens":
            raise ErroIA("resposta do Claude cortada pelo limite de tokens")
        return resposta

    def disponivel(self) -> tuple[bool, str]:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY não definida no .env"
        return True, f"chave definida, modelo {self.modelo}"


def _texto(resposta) -> str:
    texto = next((b.text for b in resposta.content if b.type == "text"), "")
    if not texto:
        raise ErroIA("resposta do Claude sem texto")
    return texto
