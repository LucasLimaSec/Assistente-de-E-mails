"""Assistente de E-mails: orquestra o fluxo diário."""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.ai_analyzer import analisar, montar_contexto
from src.config import carregar_config, validar_config
from src.email_reader import Coleta, coletar
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


def coletar_janela(cfg: dict, horas: int | None) -> tuple[Coleta, datetime]:
    agora = datetime.now(ZoneInfo(cfg["geral"]["fuso_horario"]))
    inicio = agora - timedelta(hours=horas) if horas else inicio_janela(agora, cfg)
    total_horas = (agora - inicio).total_seconds() / 3600
    print(f"Janela: {formatar_data(inicio)} → {formatar_data(agora)} ({total_horas:.0f} h)\n")
    return coletar(cfg, inicio, agora), agora


def listar_emails(caminho_config: str, horas: int | None, mostrar_descartados: bool) -> int:
    cfg = carregar_valido(caminho_config)
    if cfg is None:
        return 1
    coleta, agora = coletar_janela(cfg, horas)
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


PRIORIDADE_MARCA = {"alta": "🔴", "media": "🟡", "baixa": "🟢"}


def analisar_emails(caminho_config: str, horas: int | None, provedor: str | None) -> int:
    """Coleta e analisa os e-mails reais, só mostrando o resultado (não grava nada)."""
    cfg = carregar_valido(caminho_config)
    if cfg is None:
        return 1
    if provedor:
        cfg["llm"]["provider"] = provedor
    coleta, agora = coletar_janela(cfg, horas)
    for erro in coleta.erros:
        print(f"[FALHA] {erro}")
    if not coleta.emails:
        print("Nenhum e-mail relevante para analisar.")
        return 1 if coleta.erros else 0

    print(f"Analisando {len(coleta.emails)} e-mail(s) com {cfg['llm']['provider']} "
          f"(lotes de {cfg['llm']['lote_emails']}, reserva: {cfg['llm'].get('fallback', 'none')})...\n")
    resultado = analisar(coleta.emails, cfg, montar_contexto(cfg, agora.date()))
    por_id = {a.message_id: a for a in resultado.analises}
    emails = {e.message_id: e for e in coleta.emails}

    tarefas = [a for a in resultado.analises if a.categoria == "tarefa"]
    tarefas.sort(key=lambda a: ["alta", "media", "baixa"].index(a.prioridade))
    print(f"\n📋 TAREFAS ({len(tarefas)})")
    for a in tarefas:
        prazo = f" (prazo {a.prazo})" if a.prazo else ""
        print(f"{PRIORIDADE_MARCA[a.prioridade]} {a.tipo.capitalize()}: {a.titulo}{prazo}  [{a.provedor}]")
        if a.rascunho:
            print("    Rascunho: " + a.rascunho.replace("\n", "\n    "))

    oportunidades = sorted((a for a in resultado.analises if a.categoria == "oportunidade"),
                           key=lambda a: -a.aderencia)
    maximo = (cfg.get("oportunidades") or {}).get("max_no_relatorio", 5)
    print(f"\n💼 OPORTUNIDADES ({len(oportunidades)})")
    for a in oportunidades[:maximo]:
        print(f"• {a.aderencia:>2}/10 {a.titulo}  [{a.provedor}]")
    if len(oportunidades) > maximo:
        print(f"  +{len(oportunidades) - maximo} oportunidade(s)")

    informativos = [a for a in resultado.analises if a.categoria == "informativo"]
    print(f"\nℹ️ INFORMATIVOS ({len(informativos)})")
    for a in informativos:
        print(f"• {emails[a.message_id].assunto}")

    sem_analise = [e for e in coleta.emails if e.message_id not in por_id]
    print(f"\nTempo: {resultado.segundos / 60:.1f} min · tokens {resultado.tokens_entrada} entrada / "
          f"{resultado.tokens_saida} saída · por provedor: {resultado.por_provedor}")
    for erro in resultado.erros:
        print(f"[AVISO] {erro}")
    for e in sem_analise:
        print(f"[FALHA] sem análise: {e.assunto}")
    return 1 if sem_analise else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assistente de E-mails")
    parser.add_argument("--check", action="store_true", help="valida config, chaves e conexões e cria o banco")
    parser.add_argument("--emails", action="store_true",
                        help="lista os e-mails da janela do relatório, já filtrados (somente leitura)")
    parser.add_argument("--analisar", action="store_true",
                        help="coleta e analisa os e-mails com a IA, só mostrando o resultado (não grava nada)")
    parser.add_argument("--benchmark-ia", action="store_true",
                        help="mede tempo, memória e acertos da IA com os e-mails fictícios")
    parser.add_argument("--provedor", choices=["regras", "ollama", "claude"],
                        help="com --analisar ou --benchmark-ia: usa este provedor em vez do llm.provider")
    parser.add_argument("--horas", type=int,
                        help="com --emails ou --analisar: usa as últimas N horas em vez da janela do relatório")
    parser.add_argument("--descartados", action="store_true",
                        help="com --emails: lista também os descartados e o motivo")
    parser.add_argument("--config", default="config.yaml", help="caminho do config.yaml")
    args = parser.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    if args.check:
        return checar(args.config)
    if args.emails:
        return listar_emails(args.config, args.horas, args.descartados)
    if args.analisar:
        return analisar_emails(args.config, args.horas, args.provedor)
    if args.benchmark_ia:
        from src.benchmark import rodar
        cfg = carregar_valido(args.config)
        return rodar(cfg, args.provedor) if cfg else 1

    print("O fluxo diário ainda não foi implementado (Fase 4 em diante). "
          "Use --check, --emails, --analisar ou --benchmark-ia.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
