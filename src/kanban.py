"""Regras do quadro Kanban: criar, mover, deduplicar, marcar atraso e guardar o retrato do dia.

Colunas: A Fazer, Em Andamento, Aguardando e Feito. Cada tarefa tem um número fixo (o id).
De onde vem cada mudança fica na tabela movimentos (motivo):
  ia                 tarefa criada pela análise dos e-mails
  novo_email         e-mail novo de uma conversa que estava em Feito ou Aguardando
  email_enviado      você respondeu a conversa de uma tarefa "Responder"
  comando            /feito, /nova e afins (Fase 6) ou --mover
"""

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

from src.config import DIAS_SEMANA
from src.email_reader import Email, Enviado, assunto_sem_prefixos
from src.llm.base import Analise, sem_acento

COLUNAS = ("a_fazer", "em_andamento", "aguardando", "feito")
NOMES_COLUNAS = {"a_fazer": "A Fazer", "em_andamento": "Em Andamento", "aguardando": "Aguardando", "feito": "Feito"}
APELIDOS_COLUNAS = {
    "a_fazer": "a_fazer", "afazer": "a_fazer", "fazer": "a_fazer", "todo": "a_fazer",
    "em_andamento": "em_andamento", "andamento": "em_andamento", "fazendo": "em_andamento",
    "aguardando": "aguardando", "esperando": "aguardando",
    "feito": "feito", "feita": "feito", "concluido": "feito", "concluida": "feito",
}
ORDEM_PRIORIDADE = {"alta": 0, "media": 1, "baixa": 2}


class ErroQuadro(Exception):
    """Pedido inválido para o quadro (tarefa ou coluna inexistente)."""


@dataclass
class Tarefa:
    id: int
    tipo: str
    titulo: str
    coluna: str
    prioridade: str
    origem: str
    conta: str | None
    remetente: str | None
    prazo: str | None
    criada_em: datetime
    coluna_desde: datetime
    concluida_em: datetime | None
    rascunho: str | None = None
    dias_parada: int = 0          # dias úteis na coluna atual (só calculado em A Fazer)
    atrasada: bool = False
    prazo_vencido: bool = False


@dataclass
class Resumo:
    criadas: list[int]
    atualizadas: list[int]
    reabertas: list[int]
    concluidas: list[int]

    @classmethod
    def vazio(cls) -> "Resumo":
        return cls([], [], [], [])


# --- consultas ----------------------------------------------------------------------------------

def _data(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


def _tarefa(linha: sqlite3.Row) -> Tarefa:
    return Tarefa(
        id=linha["id"], tipo=linha["tipo"], titulo=linha["titulo"], coluna=linha["coluna"],
        prioridade=linha["prioridade"], origem=linha["origem"], conta=linha["conta"],
        remetente=linha["remetente"], prazo=linha["prazo"], criada_em=_data(linha["criada_em"]),
        coluna_desde=_data(linha["coluna_desde"]), concluida_em=_data(linha["concluida_em"]),
        rascunho=linha["rascunho"],
    )


def obter(conn: sqlite3.Connection, tarefa_id: int) -> Tarefa | None:
    linha = conn.execute("SELECT * FROM tarefas WHERE id = ?", (tarefa_id,)).fetchone()
    return _tarefa(linha) if linha else None


def abertas(conn: sqlite3.Connection) -> list[Tarefa]:
    return [_tarefa(l) for l in conn.execute("SELECT * FROM tarefas WHERE coluna != 'feito' ORDER BY id")]


def para_ia(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Tarefas abertas no formato que a IA recebe, para apontar tarefa_existente."""
    return [(t.id, t.titulo) for t in abertas(conn)]


def chave_assunto(assunto: str) -> str:
    """'RES: Orçamento  do Projeto!' -> 'orcamento do projeto'."""
    texto = sem_acento(assunto_sem_prefixos(assunto)).lower()
    return " ".join(re.sub(r"[^\w\s]", " ", texto).split())


# --- mudanças básicas ---------------------------------------------------------------------------

def coluna_valida(nome: str) -> str:
    chave = sem_acento(nome).lower().strip().replace(" ", "_").replace("-", "_")
    if chave not in APELIDOS_COLUNAS:
        raise ErroQuadro(f"coluna {nome!r} não existe; use {', '.join(COLUNAS)}")
    return APELIDOS_COLUNAS[chave]


def criar(conn: sqlite3.Connection, *, tipo: str, titulo: str, origem: str, agora: datetime,
          prioridade: str = "media", conta: str | None = None, remetente: str | None = None,
          assunto_chave: str | None = None, prazo: str | None = None, motivo: str = "comando") -> int:
    quando = agora.isoformat()
    with conn:
        cur = conn.execute(
            "INSERT INTO tarefas (tipo, titulo, prioridade, origem, conta, remetente, assunto_chave, prazo, "
            "criada_em, coluna_desde) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tipo, titulo, prioridade, origem, conta, remetente, assunto_chave, prazo or None, quando, quando),
        )
        conn.execute("INSERT INTO movimentos (tarefa_id, de_coluna, para_coluna, motivo, em) "
                     "VALUES (?, NULL, 'a_fazer', ?, ?)", (cur.lastrowid, motivo, quando))
    return cur.lastrowid


def mover(conn: sqlite3.Connection, tarefa_id: int, coluna: str, agora: datetime, motivo: str = "comando") -> bool:
    """Move a tarefa; devolve False se ela já estava na coluna."""
    coluna = coluna_valida(coluna)
    tarefa = obter(conn, tarefa_id)
    if tarefa is None:
        raise ErroQuadro(f"tarefa #{tarefa_id} não existe")
    if tarefa.coluna == coluna:
        return False
    quando = agora.isoformat()
    with conn:
        conn.execute(
            "UPDATE tarefas SET coluna = ?, coluna_desde = ?, concluida_em = ? WHERE id = ?",
            # Sair de Feito reabre a tarefa: a data de conclusão é apagada.
            (coluna, quando, quando if coluna == "feito" else None, tarefa_id),
        )
        conn.execute("INSERT INTO movimentos (tarefa_id, de_coluna, para_coluna, motivo, em) VALUES (?, ?, ?, ?, ?)",
                     (tarefa_id, tarefa.coluna, coluna, motivo, quando))
    return True


# --- e-mails ------------------------------------------------------------------------------------

def ja_registrado(conn: sqlite3.Connection, conta: str, message_id: str) -> bool:
    return conn.execute("SELECT 1 FROM emails WHERE conta = ? AND message_id = ?",
                        (conta, message_id)).fetchone() is not None


def tarefa_da_conversa(conn: sqlite3.Connection, conta: str, conversa: str, message_id: str = "") -> int | None:
    """Tarefa ligada à conversa (ou à mensagem respondida), a mais recente se houver várias."""
    linha = conn.execute(
        "SELECT tarefa_id FROM emails WHERE conta = ? AND tarefa_id IS NOT NULL AND (conversa = ? OR message_id = ?) "
        "ORDER BY tarefa_id DESC LIMIT 1", (conta, conversa, message_id or conversa)).fetchone()
    return linha[0] if linha else None


def _registrar_email(conn: sqlite3.Connection, e: Email, analise: Analise | None, tarefa_id: int | None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO emails (conta, message_id, conversa, remetente, assunto, data, enviado, tarefa_id, "
        "categoria, titulo, aderencia) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
        (e.conta, e.message_id, e.conversa, e.remetente, e.assunto, e.data.isoformat(), tarefa_id,
         analise.categoria if analise else "tarefa", analise.titulo if analise else None,
         analise.aderencia if analise else 0),
    )


def separar(conn: sqlite3.Connection, emails: list[Email]) -> tuple[list[Email], list[tuple[Email, int]], int]:
    """Divide os e-mails coletados antes da IA.

    Devolve (para a IA, de conversas com tarefa aberta, quantos já tinham sido processados).
    Conversa com tarefa aberta não passa pelo modelo: o e-mail só é anexado à tarefa.
    """
    novos, conhecidos, vistos = [], [], 0
    for e in emails:
        if ja_registrado(conn, e.conta, e.message_id):
            vistos += 1
            continue
        tarefa_id = tarefa_da_conversa(conn, e.conta, e.conversa)
        tarefa = obter(conn, tarefa_id) if tarefa_id else None
        if tarefa and tarefa.coluna != "feito":
            conhecidos.append((e, tarefa.id))
        else:
            novos.append(e)
    return novos, conhecidos, vistos


def anexar(conn: sqlite3.Connection, e: Email, tarefa_id: int, agora: datetime, resumo: Resumo,
           analise: Analise | None = None) -> None:
    """E-mail novo numa tarefa existente: atualiza prioridade e prazo e traz de volta para A Fazer."""
    tarefa = obter(conn, tarefa_id)
    if tarefa.coluna in ("feito", "aguardando"):
        # Feito: pediram algo de novo. Aguardando: chegou a resposta que você esperava.
        mover(conn, tarefa_id, "a_fazer", agora, motivo="novo_email")
        resumo.reabertas.append(tarefa_id)
    else:
        resumo.atualizadas.append(tarefa_id)
    prioridade = tarefa.prioridade
    if e.vip:
        prioridade = "alta"
    if analise and ORDEM_PRIORIDADE[analise.prioridade] < ORDEM_PRIORIDADE[prioridade]:
        prioridade = analise.prioridade
    prazo = analise.prazo if analise and analise.prazo else tarefa.prazo
    with conn:
        conn.execute("UPDATE tarefas SET prioridade = ?, prazo = ? WHERE id = ?", (prioridade, prazo, tarefa_id))
        _registrar_email(conn, e, analise, tarefa_id)


def _mesma_tarefa(conn: sqlite3.Connection, e: Email, analise: Analise) -> int | None:
    """Deduplicação, da pista mais forte para a mais fraca."""
    # 1. Mesma conversa (inclui tarefas em Feito, que são reabertas).
    tarefa_id = tarefa_da_conversa(conn, e.conta, e.conversa)
    if tarefa_id:
        return tarefa_id
    # 2. A IA apontou uma tarefa aberta do mesmo assunto.
    if analise.tarefa_existente:
        tarefa = obter(conn, analise.tarefa_existente)
        if tarefa and tarefa.coluna != "feito":
            return tarefa.id
    # 3. Mesmo remetente e mesmo assunto (sem Re:/Fwd:), ainda aberta.
    linha = conn.execute(
        "SELECT id FROM tarefas WHERE coluna != 'feito' AND conta = ? AND remetente = ? AND assunto_chave = ? "
        "ORDER BY id DESC LIMIT 1", (e.conta, e.remetente, chave_assunto(e.assunto))).fetchone()
    return linha[0] if linha else None


def aplicar(conn: sqlite3.Connection, emails: list[Email], analises: list[Analise], agora: datetime,
            resumo: Resumo | None = None) -> Resumo:
    """Grava o resultado da análise: cria tarefas, atualiza as repetidas e registra os e-mails."""
    resumo = resumo or Resumo.vazio()
    por_id = {a.message_id: a for a in analises}
    for e in emails:
        analise = por_id.get(e.message_id)
        if analise is None or ja_registrado(conn, e.conta, e.message_id):
            continue
        if analise.categoria != "tarefa":
            with conn:
                _registrar_email(conn, e, analise, None)
            continue
        existente = _mesma_tarefa(conn, e, analise)
        if existente:
            anexar(conn, e, existente, agora, resumo, analise)
            continue
        tarefa_id = criar(conn, tipo=analise.tipo, titulo=analise.titulo, origem="email", agora=agora,
                          prioridade=analise.prioridade, conta=e.conta, remetente=e.remetente,
                          assunto_chave=chave_assunto(e.assunto), prazo=analise.prazo, motivo="ia")
        with conn:
            _registrar_email(conn, e, analise, tarefa_id)
        resumo.criadas.append(tarefa_id)
    return resumo


def registrar_enviados(conn: sqlite3.Connection, enviados: list[Enviado], agora: datetime,
                       resumo: Resumo | None = None) -> Resumo:
    """Resposta enviada numa conversa de tarefa "Responder" move a tarefa para Feito."""
    resumo = resumo or Resumo.vazio()
    for s in enviados:
        if ja_registrado(conn, s.conta, s.message_id):
            continue
        tarefa_id = tarefa_da_conversa(conn, s.conta, s.conversa, s.responde_a)
        tarefa = obter(conn, tarefa_id) if tarefa_id else None
        if tarefa and tarefa.tipo == "responder" and tarefa.coluna in ("a_fazer", "em_andamento") \
                and s.data > _ultimo_recebido(conn, tarefa.id):
            mover(conn, tarefa.id, "feito", agora, motivo="email_enviado")
            resumo.concluidas.append(tarefa.id)
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO emails (conta, message_id, conversa, assunto, data, enviado, tarefa_id) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (s.conta, s.message_id, s.conversa, s.assunto, s.data.isoformat(), tarefa_id))
    return resumo


def _ultimo_recebido(conn: sqlite3.Connection, tarefa_id: int) -> datetime:
    """Data do e-mail recebido mais recente da tarefa: só uma resposta posterior conta como feita."""
    datas = [datetime.fromisoformat(l[0]) for l in
             conn.execute("SELECT data FROM emails WHERE tarefa_id = ? AND enviado = 0", (tarefa_id,))]
    return max(datas) if datas else datetime.min.replace(tzinfo=timezone.utc)


def email_da_tarefa(conn: sqlite3.Connection, tarefa_id: int) -> tuple[str, str] | None:
    """(conta, Message-ID) do e-mail recebido mais recente da tarefa, para o /rascunho."""
    linhas = conn.execute("SELECT conta, message_id, data FROM emails WHERE tarefa_id = ? AND enviado = 0",
                          (tarefa_id,)).fetchall()
    if not linhas:
        return None
    ultima = max(linhas, key=lambda l: datetime.fromisoformat(l["data"]))
    return ultima["conta"], ultima["message_id"]


def salvar_rascunho(conn: sqlite3.Connection, tarefa_id: int, texto: str) -> None:
    with conn:
        conn.execute("UPDATE tarefas SET rascunho = ? WHERE id = ?", (texto, tarefa_id))


def nova_nota(conn: sqlite3.Connection, texto: str, agora: datetime) -> int:
    with conn:
        cur = conn.execute("INSERT INTO notas (texto, criada_em) VALUES (?, ?)", (texto.strip(), agora.isoformat()))
    return cur.lastrowid


def notas_desde(conn: sqlite3.Connection, desde: datetime) -> list[str]:
    linhas = conn.execute("SELECT texto, criada_em FROM notas ORDER BY id").fetchall()
    return [l["texto"] for l in linhas if datetime.fromisoformat(l["criada_em"]) >= desde]


def concluidas_desde(conn: sqlite3.Connection, desde: datetime) -> list[Tarefa]:
    return [_tarefa(l) for l in conn.execute(
        "SELECT * FROM tarefas WHERE coluna = 'feito' AND concluida_em IS NOT NULL ORDER BY concluida_em")
        if _data(l["concluida_em"]) >= desde]


def oportunidades(conn: sqlite3.Connection, desde: datetime) -> list[tuple[str, int]]:
    """(título, aderência) das oportunidades recebidas desde `desde`, da maior aderência para a menor."""
    # Datas gravadas com fusos diferentes: a comparação é feita em Python, não no texto.
    linhas = [(l["titulo"] or l["assunto"], l["aderencia"]) for l in conn.execute(
        "SELECT titulo, assunto, aderencia, data FROM emails WHERE categoria = 'oportunidade' AND enviado = 0")
        if datetime.fromisoformat(l["data"]) >= desde]
    return sorted(linhas, key=lambda o: -o[1])


# --- atraso e quadro ----------------------------------------------------------------------------

def dias_uteis_entre(inicio: date, fim: date, dias_uteis: set[int]) -> int:
    """Dias úteis depois de `inicio` até `fim`, inclusive (criada na segunda, quinta = 3)."""
    dias, dia = 0, inicio + timedelta(days=1)
    while dia <= fim:
        dias += dia.weekday() in dias_uteis
        dia += timedelta(days=1)
    return dias


def marcar_atraso(tarefas: list[Tarefa], agora: datetime, cfg: dict) -> None:
    """Tarefa em A Fazer há mais de `dias_para_atraso` dias úteis ganha a marca de atraso."""
    dias_uteis = {DIAS_SEMANA.index(d) for d in cfg["geral"]["dias_uteis"]}
    limite = cfg["kanban"]["dias_para_atraso"]
    hoje = agora.date()
    for t in tarefas:
        t.prazo_vencido = bool(t.prazo) and t.coluna != "feito" and t.prazo < hoje.isoformat()
        if t.coluna != "a_fazer":
            continue
        t.dias_parada = dias_uteis_entre(t.coluna_desde.astimezone(agora.tzinfo).date(), hoje, dias_uteis)
        t.atrasada = t.dias_parada > limite


def quadro(conn: sqlite3.Connection, agora: datetime, cfg: dict, feito_desde: datetime) -> dict[str, list[Tarefa]]:
    """Colunas do quadro, ordenadas por prioridade e prazo. Feito só mostra o que foi concluído desde `feito_desde`."""
    tarefas = abertas(conn) + concluidas_desde(conn, feito_desde)
    marcar_atraso(tarefas, agora, cfg)
    colunas = {c: [] for c in COLUNAS}
    for t in sorted(tarefas, key=lambda t: (ORDEM_PRIORIDADE[t.prioridade], t.prazo or "9999-12-31", t.id)):
        colunas[t.coluna].append(t)
    return colunas


def salvar_snapshot(conn: sqlite3.Connection, dia: date, colunas: dict[str, list[Tarefa]]) -> None:
    """Retrato do quadro do dia (um por data; rodar de novo no mesmo dia substitui)."""
    retrato = {c: [{k: v for k, v in asdict(t).items() if k in ("id", "titulo", "tipo", "prioridade", "dias_parada",
                                                                 "atrasada")} for t in ts]
               for c, ts in colunas.items()}
    with conn:
        conn.execute("INSERT OR REPLACE INTO snapshots (data, quadro) VALUES (?, ?)",
                     (dia.isoformat(), json.dumps(retrato, ensure_ascii=False)))
