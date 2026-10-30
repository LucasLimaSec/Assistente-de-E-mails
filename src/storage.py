"""SQLite: tarefas, e-mails vistos, histórico de movimentos, notas, snapshots e execuções.

Datas são gravadas como texto ISO 8601 com fuso (ex.: 2026-10-29T07:00:00-03:00).
"""

import sqlite3
from pathlib import Path

VERSAO_SCHEMA = 1

SCHEMA = """
CREATE TABLE tarefas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,  -- número fixo usado nos comandos (/feito 12)
    tipo          TEXT NOT NULL CHECK (tipo IN ('responder', 'realizar')),
    titulo        TEXT NOT NULL,
    coluna        TEXT NOT NULL DEFAULT 'a_fazer'
                  CHECK (coluna IN ('a_fazer', 'em_andamento', 'aguardando', 'feito')),
    prioridade    TEXT NOT NULL DEFAULT 'media' CHECK (prioridade IN ('alta', 'media', 'baixa')),
    origem        TEXT NOT NULL CHECK (origem IN ('email', 'telegram', 'agenda')),
    conta         TEXT,             -- nome da conta no config.yaml
    remetente     TEXT,
    assunto_chave TEXT,             -- assunto normalizado, para não duplicar tarefas
    rascunho      TEXT,             -- rascunho de resposta sugerido pela IA (/rascunho N)
    prazo         TEXT,
    criada_em     TEXT NOT NULL,
    coluna_desde  TEXT NOT NULL,    -- entrada na coluna atual; base do cálculo de atraso
    concluida_em  TEXT
);
CREATE INDEX idx_tarefas_coluna ON tarefas (coluna);
CREATE INDEX idx_tarefas_assunto ON tarefas (assunto_chave);

-- E-mails já processados, recebidos ou enviados por você.
CREATE TABLE emails (
    conta       TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    conversa    TEXT,               -- Message-ID raiz da conversa (References/In-Reply-To)
    remetente   TEXT,
    assunto     TEXT,
    data        TEXT NOT NULL,
    enviado     INTEGER NOT NULL DEFAULT 0,   -- 1 = veio da pasta de enviados
    tarefa_id   INTEGER REFERENCES tarefas (id),
    PRIMARY KEY (conta, message_id)
);
CREATE INDEX idx_emails_conversa ON emails (conversa);

CREATE TABLE movimentos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tarefa_id   INTEGER NOT NULL REFERENCES tarefas (id),
    de_coluna   TEXT,               -- NULL na criação
    para_coluna TEXT NOT NULL,
    motivo      TEXT NOT NULL,      -- ia | comando | email_enviado | resposta_recebida
    em          TEXT NOT NULL
);
CREATE INDEX idx_movimentos_em ON movimentos (em);

-- Notas livres mandadas ao bot ("Fechei o orçamento com o cliente X").
CREATE TABLE notas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    texto     TEXT NOT NULL,
    criada_em TEXT NOT NULL
);

-- Retrato do quadro (JSON) após cada relatório.
CREATE TABLE snapshots (
    data   TEXT PRIMARY KEY,        -- AAAA-MM-DD
    quadro TEXT NOT NULL
);

-- Cada execução do relatório; a última com status 'ok' define a janela de leitura da próxima.
CREATE TABLE execucoes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo           TEXT NOT NULL CHECK (tipo IN ('diario', 'semanal')),
    iniciada_em    TEXT NOT NULL,
    terminada_em   TEXT,
    status         TEXT NOT NULL DEFAULT 'em_andamento'
                   CHECK (status IN ('em_andamento', 'ok', 'falha')),
    tokens_entrada INTEGER NOT NULL DEFAULT 0,
    tokens_saida   INTEGER NOT NULL DEFAULT 0,
    erro           TEXT
);
"""


def conectar(caminho: str | Path) -> sqlite3.Connection:
    """Abre o banco (criando a pasta se preciso) e garante o schema."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL permite o bot e o relatório usarem o banco ao mesmo tempo.
    conn.execute("PRAGMA journal_mode = WAL")
    inicializar(conn)
    return conn


def inicializar(conn: sqlite3.Connection) -> None:
    versao = conn.execute("PRAGMA user_version").fetchone()[0]
    if versao >= VERSAO_SCHEMA:
        return
    with conn:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {VERSAO_SCHEMA}")


def tabelas(conn: sqlite3.Connection) -> list[str]:
    linhas = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [linha[0] for linha in linhas]
