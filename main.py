"""Assistente de E-mails: orquestra o fluxo diário."""

import argparse
import sys

from dotenv import load_dotenv

from src.config import carregar_config
from src.verificacao import verificar_tudo


def checar(caminho_config: str) -> int:
    try:
        cfg = carregar_config(caminho_config)
    except (OSError, ValueError) as e:
        print(f"[FALHA] Config: não foi possível ler {caminho_config} ({e})")
        return 1

    resultados = verificar_tudo(cfg)
    for r in resultados:
        print(f"[{' OK ' if r.ok else 'FALHA'}] {r.nome}: {r.detalhe}")
    falhas = sum(not r.ok for r in resultados)
    print(f"\n{len(resultados) - falhas} ok, {falhas} com falha.")
    return 1 if falhas else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assistente de E-mails")
    parser.add_argument("--check", action="store_true", help="valida config, chaves e conexões e cria o banco")
    parser.add_argument("--config", default="config.yaml", help="caminho do config.yaml")
    args = parser.parse_args(argv)

    load_dotenv()
    if args.check:
        return checar(args.config)

    print("O fluxo diário ainda não foi implementado (Fase 2 em diante). Use --check.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
