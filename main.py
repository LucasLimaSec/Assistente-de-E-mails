"""Assistente de E-mails: orquestra o fluxo diário."""

import argparse
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.config import carregar_config, validar_config
from src.email_reader import coletar
from src.janela import inicio_janela
from src.verificacao import verificar_tudo

DIAS_ABREV = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def carregar_valido(caminho_config: str) -> dict | None:
    try:
        cfg = carregar_config(caminho_config)
    except (OSError, ValueError) as e:
        print(f"[FALHA] Config: não foi possível ler {caminho_config} ({e})")
        return None
    erros = validar_config(cfg)
    if erros:
        print("[FALHA] Config: " + "; ".join(erros))
        return None
    return cfg


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


def formatar_data(quando: datetime) -> str:
    return f"{DIAS_ABREV[quando.weekday()]} {quando:%d/%m %H:%M}"


def listar_emails(caminho_config: str, horas: int | None, mostrar_descartados: bool) -> int:
    cfg = carregar_valido(caminho_config)
    if cfg is None:
        return 1
    agora = datetime.now(ZoneInfo(cfg["geral"]["fuso_horario"]))
    inicio = agora - timedelta(hours=horas) if horas else inicio_janela(agora, cfg)
    total_horas = (agora - inicio).total_seconds() / 3600
    print(f"Janela: {formatar_data(inicio)} → {formatar_data(agora)} ({total_horas:.0f} h)\n")

    coleta = coletar(cfg, inicio, agora)
    fuso = agora.tzinfo

    print(f"{len(coleta.emails)} e-mail(s) relevante(s):")
    for e in coleta.emails:
        marca = "⭐" if e.vip else "•"
        remetente = f"{e.remetente_nome} <{e.remetente}>" if e.remetente_nome else e.remetente
        print(f"\n{marca} [{e.conta}] {formatar_data(e.data.astimezone(fuso))}  {remetente}")
        print(f"  Assunto: {e.assunto}")
        resumo = " ".join(e.corpo.split())
        print(f"  {resumo[:160]}{'…' if len(resumo) > 160 else ''}")

    if coleta.descartados:
        motivos = {}
        for d in coleta.descartados:
            motivos[d.motivo] = motivos.get(d.motivo, 0) + 1
        contagem = ", ".join(f"{motivo}: {n}" for motivo, n in sorted(motivos.items(), key=lambda m: -m[1]))
        print(f"\nDescartados: {len(coleta.descartados)} ({contagem})")
        if mostrar_descartados:
            for d in sorted(coleta.descartados, key=lambda d: d.data):
                print(f"  - [{d.motivo}] {formatar_data(d.data.astimezone(fuso))}  {d.remetente}  {d.assunto}")
        else:
            print("  Use --descartados para ver a lista.")

    for erro in coleta.erros:
        print(f"\n[FALHA] {erro}")
    return 1 if coleta.erros else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assistente de E-mails")
    parser.add_argument("--check", action="store_true", help="valida config, chaves e conexões e cria o banco")
    parser.add_argument("--emails", action="store_true",
                        help="lista os e-mails da janela do relatório, já filtrados (somente leitura)")
    parser.add_argument("--horas", type=int, help="com --emails: usa as últimas N horas em vez da janela do relatório")
    parser.add_argument("--descartados", action="store_true",
                        help="com --emails: lista também os descartados e o motivo")
    parser.add_argument("--config", default="config.yaml", help="caminho do config.yaml")
    args = parser.parse_args(argv)

    load_dotenv()
    if args.check:
        return checar(args.config)
    if args.emails:
        return listar_emails(args.config, args.horas, args.descartados)

    print("O fluxo diário ainda não foi implementado (Fase 3 em diante). Use --check ou --emails.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
