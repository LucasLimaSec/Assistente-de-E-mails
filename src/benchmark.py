"""--benchmark-ia: mede tempo, memória e acertos da IA com os e-mails fictícios de tests/fixtures."""

import email
import time
from datetime import date
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

from src.config import perfil
from src.email_reader import Email, montar_email
from src.llm import criar_provedor
from src.llm.base import Analise, Contexto, ErroIA

PASTA_AMOSTRAS = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
HOJE_AMOSTRAS = date(2026, 10, 30)   # data dos e-mails fictícios, para os prazos fazerem sentido
PERFIL_EXEMPLO = "Pentest web e de APIs, desenvolvimento seguro, Python, OWASP"
EMAILS_POR_DIA = 30                  # para estimar a duração da análise diária


def carregar_amostras(pasta: Path = PASTA_AMOSTRAS) -> list[tuple[Email, dict]]:
    esperado = yaml.safe_load((pasta / "esperado.yaml").read_text(encoding="utf-8"))
    amostras = []
    for arquivo, gabarito in esperado.items():
        msg = email.message_from_bytes((pasta / arquivo).read_bytes(), policy=policy.default)
        recebido = parsedate_to_datetime(str(msg["Date"]))
        amostras.append((montar_email(msg, "amostra", arquivo, recebido, {}, 1500), gabarito))
    return amostras


def conferir(analise: Analise | None, gabarito: dict) -> dict[str, bool]:
    """Quais campos do gabarito a análise acertou."""
    if analise is None:
        return {campo: False for campo in gabarito}
    acertos = {}
    for campo, esperado in gabarito.items():
        if campo == "aderencia_min":
            acertos[campo] = analise.aderencia >= esperado
        elif campo == "aderencia_max":
            acertos[campo] = analise.aderencia <= esperado
        else:
            acertos[campo] = getattr(analise, campo) == esperado
    return acertos


def rodar(cfg: dict, nome_provedor: str | None = None) -> int:
    nome = nome_provedor or cfg["llm"]["provider"]
    provedor = criar_provedor(nome, cfg)
    ok, detalhe = provedor.disponivel()
    if not ok:
        print(f"[FALHA] IA ({nome}): {detalhe}")
        return 1

    amostras = carregar_amostras()
    contexto = Contexto(hoje=HOJE_AMOSTRAS, perfil=perfil(cfg) or PERFIL_EXEMPLO)
    tamanho = cfg["llm"]["lote_emails"]
    print(f"Benchmark: {len(amostras)} e-mails fictícios, provedor {nome}, lotes de {tamanho}")
    print(f"Perfil usado: {contexto.perfil}\n")

    analises: dict[str, Analise] = {}
    entrada = saida = 0
    tempo_total = 0.0
    falhas = 0
    for i in range(0, len(amostras), tamanho):
        lote = [e for e, _ in amostras[i:i + tamanho]]
        inicio = time.monotonic()
        try:
            resultado = provedor.analisar(lote, contexto)
        except ErroIA as e:
            duracao = time.monotonic() - inicio
            tempo_total += duracao
            falhas += 1
            print(f"  Lote {i // tamanho + 1}: FALHOU em {duracao:.1f} s ({e})")
            continue
        duracao = time.monotonic() - inicio
        tempo_total += duracao
        entrada += resultado.tokens_entrada
        saida += resultado.tokens_saida
        analises.update({a.message_id: a for a in resultado.analises})
        velocidade = f", {resultado.tokens_saida / duracao:.1f} tokens/s na saída" if resultado.tokens_saida else ""
        print(f"  Lote {i // tamanho + 1}: {len(resultado.analises)}/{len(lote)} e-mails em {duracao:.1f} s{velocidade}")

    memoria = getattr(provedor, "memoria_em_uso", lambda: None)()

    print("\nResultado por e-mail (✓ acertou o gabarito, ✗ errou):")
    totais: dict[str, list[int]] = {}
    for e, gabarito in amostras:
        a = analises.get(e.message_id)
        acertos = conferir(a, gabarito)
        for campo, certo in acertos.items():
            chave = "aderencia" if campo.startswith("aderencia") else campo
            totais.setdefault(chave, [0, 0])
            totais[chave][0] += certo
            totais[chave][1] += 1
        marca = "✓" if all(acertos.values()) else "✗"
        if a is None:
            print(f"  {marca} {e.assunto[:55]:<55} sem resposta válida")
            continue
        extra = f" aderência {a.aderencia}" if a.categoria == "oportunidade" else ""
        prazo = f" prazo {a.prazo}" if a.prazo else ""
        print(f"  {marca} {e.assunto[:55]:<55} {a.categoria}/{a.tipo}/{a.prioridade}{extra}{prazo}")
        print(f"      título: {a.titulo}")

    print("\nAcertos:", " · ".join(f"{campo} {c}/{t}" for campo, (c, t) in totais.items()))
    respondidos = len(analises)
    if tempo_total:
        # Tempo por e-mail enviado (inclui lotes que falharam): é o que a madrugada vai gastar.
        por_email = tempo_total / len(amostras)
        print(f"Tempo: {tempo_total:.0f} s no total, {por_email:.1f} s por e-mail "
              f"(~{por_email * EMAILS_POR_DIA / 60:.0f} min para {EMAILS_POR_DIA} e-mails por dia)")
    print(f"Tokens: {entrada} entrada / {saida} saída")
    if memoria:
        print(f"Memória do modelo carregado: {memoria / 1024 ** 3:.1f} GB")
    if falhas:
        print(f"Lotes com falha: {falhas}")

    _medir_rascunho(provedor, amostras, contexto)
    return 1 if falhas or respondidos < len(amostras) else 0


def _medir_rascunho(provedor, amostras: list[tuple[Email, dict]], contexto: Contexto) -> None:
    alvo = next((e for e, g in amostras if g.get("tipo") == "responder"), None)
    if alvo is None or provedor.nome == "regras":
        return
    print(f"\nRascunho sob demanda para: {alvo.assunto}")
    inicio = time.monotonic()
    try:
        texto, _, _ = provedor.rascunhar(alvo, contexto)
    except ErroIA as e:
        print(f"  FALHOU ({e})")
        return
    print(f"  Gerado em {time.monotonic() - inicio:.1f} s:\n")
    print("  " + texto.replace("\n", "\n  "))
