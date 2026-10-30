import sqlite3

import pytest

from src import storage

TABELAS = ["emails", "execucoes", "movimentos", "notas", "snapshots", "tarefas"]
AGORA = "2026-10-29T07:00:00-03:00"


@pytest.fixture
def conn(tmp_path):
    conn = storage.conectar(tmp_path / "sub" / "teste.db")
    yield conn
    conn.close()


def nova_tarefa(conn, **campos):
    dados = {"tipo": "responder", "titulo": "Maria - aprovar orçamento", "origem": "email",
             "criada_em": AGORA, "coluna_desde": AGORA, **campos}
    colunas = ", ".join(dados)
    marcadores = ", ".join("?" * len(dados))
    with conn:
        cur = conn.execute(f"INSERT INTO tarefas ({colunas}) VALUES ({marcadores})", list(dados.values()))
    return cur.lastrowid


def test_cria_pasta_e_tabelas(conn):
    assert storage.tabelas(conn) == TABELAS
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.VERSAO_SCHEMA


def test_reabrir_nao_recria_nem_apaga(tmp_path):
    caminho = tmp_path / "teste.db"
    conn = storage.conectar(caminho)
    nova_tarefa(conn)
    conn.close()

    conn = storage.conectar(caminho)
    assert conn.execute("SELECT COUNT(*) FROM tarefas").fetchone()[0] == 1
    conn.close()


def test_tarefa_nova_usa_padroes(conn):
    tarefa_id = nova_tarefa(conn)
    tarefa = conn.execute("SELECT * FROM tarefas WHERE id = ?", (tarefa_id,)).fetchone()
    assert tarefa["coluna"] == "a_fazer"
    assert tarefa["prioridade"] == "media"


def test_numeros_de_tarefa_nao_sao_reaproveitados(conn):
    primeira = nova_tarefa(conn)
    with conn:
        conn.execute("DELETE FROM tarefas WHERE id = ?", (primeira,))
    assert nova_tarefa(conn) == primeira + 1


@pytest.mark.parametrize("campo, valor", [
    ("coluna", "lixo"),
    ("prioridade", "urgente"),
    ("tipo", "ligar"),
    ("origem", "whatsapp"),
])
def test_rejeita_valores_fora_das_regras(conn, campo, valor):
    with pytest.raises(sqlite3.IntegrityError):
        nova_tarefa(conn, **{campo: valor})


def test_movimento_exige_tarefa_existente(conn):
    with pytest.raises(sqlite3.IntegrityError), conn:
        conn.execute(
            "INSERT INTO movimentos (tarefa_id, para_coluna, motivo, em) VALUES (999, 'feito', 'comando', ?)",
            (AGORA,),
        )
