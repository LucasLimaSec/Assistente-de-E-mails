"""Monta o relatório diário em HTML do Telegram (negrito com <b>, o resto texto puro)."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape

from src.kanban import Tarefa

DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
DIAS_CURTOS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
MARCA_PRIORIDADE = {"alta": "🔴", "media": "🟡", "baixa": "🟢"}
NOME_TIPO = {"responder": "Responder", "realizar": "Realizar"}
LIMITE_TELEGRAM = 4096


@dataclass
class Oportunidade:
    titulo: str
    aderencia: int


@dataclass
class DadosRelatorio:
    agora: datetime
    desde: datetime                       # início da janela (relatório do último dia útil)
    colunas: dict[str, list[Tarefa]]
    oportunidades: list[Oportunidade] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    marcar_conta: bool = False            # com mais de uma conta, cada item mostra a conta


def montar(dados: DadosRelatorio, max_itens: int = 5, max_oportunidades: int = 5) -> str:
    hoje = dados.agora.date()
    partes = [_cabecalho(dados)]

    feitas = dados.colunas["feito"]
    if feitas:
        titulo = "FEITO ONTEM" if dados.desde.date() == hoje - timedelta(days=1) \
            else f"FEITO DESDE {DIAS[dados.desde.weekday()].upper()}"
        partes.append(_secao(f"✅ {titulo}", [f"• {_texto(t, dados)}" for t in feitas], max_itens))

    a_fazer = dados.colunas["a_fazer"]
    if a_fazer:
        partes.append(_secao("📋 A FAZER", [_linha_a_fazer(t, hoje, dados) for t in a_fazer], max_itens))
    else:
        partes.append("<b>📋 A FAZER</b>\nNada pendente. 🎉")

    for coluna, titulo in (("em_andamento", "🔄 EM ANDAMENTO"), ("aguardando", "⏳ AGUARDANDO")):
        if dados.colunas[coluna]:
            linhas = [f"• #{t.id} {_texto(t, dados)}{_prazo(t, hoje)}" for t in dados.colunas[coluna]]
            partes.append(_secao(titulo, linhas, max_itens))

    if dados.oportunidades:
        linhas = [f"• {o.aderencia}/10 {escape(o.titulo)}"
                  for o in sorted(dados.oportunidades, key=lambda o: -o.aderencia)]
        partes.append(_secao("💼 OPORTUNIDADES", linhas, max_oportunidades, "oportunidade(s)"))

    foco = foco_do_dia(a_fazer + dados.colunas["em_andamento"], hoje)
    if foco:
        partes.append(f"💡 <b>Foco do dia:</b> {foco}")

    if dados.avisos:
        partes.append("\n".join(f"⚠️ {escape(a)}" for a in dados.avisos))
    return "\n\n".join(partes)


def _cabecalho(dados: DadosRelatorio) -> str:
    hoje = dados.agora
    abertas = dados.colunas["a_fazer"] + dados.colunas["em_andamento"]
    contagem = []
    for tipo, texto in (("responder", "para responder"), ("realizar", "para realizar")):
        n = sum(t.tipo == tipo for t in abertas)
        if n:
            contagem.append(f"{n} {texto}")
    atrasadas = sum(t.atrasada for t in dados.colunas["a_fazer"])
    if atrasadas:
        contagem.append(f"{atrasadas} atrasada{'s' if atrasadas > 1 else ''}")
    linha = " · ".join(contagem) or "Quadro em dia"
    return f"☀️ <b>Bom dia! {DIAS[hoje.weekday()]}, {hoje:%d/%m}</b>\n{linha}"


def _secao(titulo: str, linhas: list[str], maximo: int, nome_resto: str = "tarefa(s)") -> str:
    corpo = linhas[:maximo]
    if len(linhas) > maximo:
        corpo.append(f"+{len(linhas) - maximo} {nome_resto}")
    return f"<b>{titulo}</b>\n" + "\n".join(corpo)


def _texto(t: Tarefa, dados: DadosRelatorio) -> str:
    conta = f" [{escape(t.conta)}]" if dados.marcar_conta and t.conta else ""
    return escape(t.titulo) + conta


def _linha_a_fazer(t: Tarefa, hoje: date, dados: DadosRelatorio) -> str:
    if t.atrasada:
        return f"⚠️ #{t.id} {NOME_TIPO[t.tipo]}: {_texto(t, dados)} (há {t.dias_parada} dias){_prazo(t, hoje)}"
    return f"{MARCA_PRIORIDADE[t.prioridade]} #{t.id} {NOME_TIPO[t.tipo]}: {_texto(t, dados)}{_prazo(t, hoje)}"


def _prazo(t: Tarefa, hoje: date) -> str:
    if not t.prazo:
        return ""
    prazo = date.fromisoformat(t.prazo)
    if prazo < hoje:
        return f" (venceu {prazo:%d/%m})"
    if prazo == hoje:
        return " (hoje)"
    if prazo == hoje + timedelta(days=1):
        return " (amanhã)"
    return f" (até {DIAS_CURTOS[prazo.weekday()]} {prazo:%d/%m})"


def foco_do_dia(tarefas: list[Tarefa], hoje: date) -> str | None:
    """A tarefa mais urgente: prazo vencido ou de hoje, depois atrasada, depois prioridade alta."""
    if not tarefas:
        return None

    def urgencia(t: Tarefa):
        prazo = date.fromisoformat(t.prazo) if t.prazo else date.max
        return (prazo > hoje, not t.atrasada, {"alta": 0, "media": 1, "baixa": 2}[t.prioridade], prazo,
                -t.dias_parada, t.id)

    t = min(tarefas, key=urgencia)
    return f"#{t.id} {escape(t.titulo)}{_prazo(t, hoje)}."


def dividir(texto: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    """Quebra em mensagens de até `limite` caracteres, sempre entre seções."""
    mensagens, atual = [], ""
    for parte in texto.split("\n\n"):
        candidato = f"{atual}\n\n{parte}" if atual else parte
        if len(candidato) <= limite:
            atual = candidato
            continue
        if atual:
            mensagens.append(atual)
        while len(parte) > limite:        # seção sozinha maior que o limite: corta por linha
            corte = parte.rfind("\n", 0, limite)
            corte = corte if corte > 0 else limite
            mensagens.append(parte[:corte])
            parte = parte[corte:].lstrip("\n")
        atual = parte
    if atual:
        mensagens.append(atual)
    return mensagens
