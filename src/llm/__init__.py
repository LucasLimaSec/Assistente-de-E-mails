"""Provedores de IA trocáveis pelo config.yaml (llm.provider e llm.fallback)."""

from src.llm.base import Provedor


def criar_provedor(nome: str, cfg: dict) -> Provedor:
    pasta_prompts = cfg["_pasta"] / "prompts"
    if nome == "regras":
        from src.llm.regras import ProvedorRegras
        return ProvedorRegras(cfg)
    if nome == "ollama":
        from src.llm.ollama import ProvedorOllama
        return ProvedorOllama(cfg["llm"]["ollama"], pasta_prompts)
    if nome == "claude":
        from src.llm.claude import ProvedorClaude
        return ProvedorClaude(cfg["llm"]["claude"], pasta_prompts)
    raise ValueError(f"provedor de IA desconhecido: {nome!r}")
