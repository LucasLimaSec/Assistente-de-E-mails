"""Provedor local: Ollama pela API HTTP (no container do servidor ou, no futuro, no PC de mesa)."""

import json
import urllib.error
import urllib.request
from pathlib import Path

from src.llm.base import ErroIA, ProvedorLLM, ler_json

TIMEOUT_CONSULTA = 10


class ProvedorOllama(ProvedorLLM):
    nome = "ollama"

    def __init__(self, cfg_ollama: dict, pasta_prompts: Path):
        super().__init__(pasta_prompts)
        self.url = cfg_ollama["url"].rstrip("/")
        self.modelo = cfg_ollama["model"]
        self.num_ctx = cfg_ollama.get("num_ctx", 8192)
        self.timeout = cfg_ollama.get("timeout_segundos", 900)
        self.keep_alive = str(cfg_ollama.get("keep_alive", "5m"))
        self.think = cfg_ollama.get("think")

    def _gerar_json(self, sistema: str, usuario: str, esquema: dict, max_saida: int) -> tuple[dict, int, int]:
        resposta = self._conversar(sistema, usuario, max_saida, formato=esquema)
        if resposta.get("done_reason") == "length":
            raise ErroIA(f"resposta cortada no limite de {max_saida} tokens (o modelo entrou em loop?)")
        return ler_json(resposta["message"]["content"]), *_tokens(resposta)

    def _gerar_texto(self, sistema: str, usuario: str, max_saida: int) -> tuple[str, int, int]:
        resposta = self._conversar(sistema, usuario, max_saida)
        return resposta["message"]["content"], *_tokens(resposta)

    def _conversar(self, sistema: str, usuario: str, max_saida: int, formato: dict | None = None) -> dict:
        corpo = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "num_predict": max_saida, "temperature": 0},
        }
        if formato:
            corpo["format"] = formato
        if self.think is not None:
            corpo["think"] = self.think
        resposta = self._requisitar("/api/chat", corpo, self.timeout)
        if not isinstance(resposta.get("message"), dict) or "content" not in resposta["message"]:
            raise ErroIA("resposta do Ollama sem mensagem")
        return resposta

    def _requisitar(self, caminho: str, corpo: dict | None, timeout: float) -> dict:
        dados = json.dumps(corpo).encode() if corpo is not None else None
        pedido = urllib.request.Request(self.url + caminho, data=dados, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
                return json.load(resposta)
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode(errors="replace")[:200]
            raise ErroIA(f"Ollama respondeu {e.code}: {detalhe}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ErroIA(f"sem conexão com o Ollama em {self.url} ({e})") from e
        except json.JSONDecodeError as e:
            raise ErroIA("Ollama devolveu algo que não é JSON") from e

    def disponivel(self) -> tuple[bool, str]:
        try:
            modelos = self._requisitar("/api/tags", None, TIMEOUT_CONSULTA).get("models", [])
        except ErroIA as e:
            return False, str(e)
        nomes = {m.get("name") for m in modelos} | {m.get("model") for m in modelos}
        # "qwen3.5" sem tag equivale a "qwen3.5:latest".
        if self.modelo not in nomes and f"{self.modelo}:latest" not in nomes:
            return False, (f"modelo {self.modelo} não baixado; rode: "
                           f"docker compose exec ollama ollama pull {self.modelo}")
        return True, f"modelo {self.modelo} disponível em {self.url}"

    def memoria_em_uso(self) -> int | None:
        """Bytes ocupados pelos modelos carregados agora (None se não der para consultar)."""
        try:
            carregados = self._requisitar("/api/ps", None, TIMEOUT_CONSULTA).get("models", [])
        except ErroIA:
            return None
        return sum(int(m.get("size", 0)) for m in carregados)


def _tokens(resposta: dict) -> tuple[int, int]:
    return int(resposta.get("prompt_eval_count", 0)), int(resposta.get("eval_count", 0))
