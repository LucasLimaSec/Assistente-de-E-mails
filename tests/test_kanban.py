import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src import kanban, storage
from src.ai_analyzer import ResultadoAnalise
from src.email_reader import Coleta, Email, Enviado
from src.fluxo import atualizar_quadro
from src.llm.base import Analise

BRT = timezone(timedelta(hours=-3))
SEG = datetime(2026, 11, 2, 8, 0, tzinfo=BRT)   # segunda-feira
CFG = {"geral": {"dias_uteis": ["seg", "ter", "qua", "qui", "sex", "sab"]}, "kanban": {"dias_para_atraso": 3}}


@pytest.fixture
def conn(tmp_path):
    conn = storage.conectar(tmp_path / "teste.db")
    yield conn
    conn.close()


def email_(n, assunto="Orçamento", remetente="maria@example.com", conversa=None, quando=SEG, vip=False):
    return Email(conta="pessoal", uid=str(n), message_id=f"<m{n}@example.com>",
                 conversa=conversa or f"<m{n}@example.com>", remetente_nome="Maria", remetente=remetente,
                 assunto=assunto, data=quando - timedelta(hours=1), corpo="...", vip=vip)


def analise(e, categoria="tarefa", tipo="responder", prioridade="media", titulo="Aprovar orçamento da Maria",
            **campos):
    return Analise(message_id=e.message_id, categoria=categoria, tipo=tipo, prioridade=prioridade, titulo=titulo,
                   **campos)


def criar_simples(conn, agora=SEG, **campos):
    return kanban.criar(conn, tipo="realizar", titulo="Emitir nota", origem="telegram", agora=agora, **campos)


def movimentos(conn, tarefa_id):
    return [(m["de_coluna"], m["para_coluna"], m["motivo"])
            for m in conn.execute("SELECT * FROM movimentos WHERE tarefa_id = ? ORDER BY id", (tarefa_id,))]


# --- mover --------------------------------------------------------------------------------------

def test_criar_comeca_em_a_fazer_com_numero_fixo(conn):
    a, b = criar_simples(conn), criar_simples(conn)
    assert (a, b) == (1, 2)
    assert kanban.obter(conn, a).coluna == "a_fazer"
    assert movimentos(conn, a) == [(None, "a_fazer", "comando")]


def test_mover_registra_historico_e_conclusao(conn):
    t = criar_simples(conn)
    depois = SEG + timedelta(hours=3)
    assert kanban.mover(conn, t, "em andamento", depois)
    assert kanban.mover(conn, t, "feito", depois)
    tarefa = kanban.obter(conn, t)
    assert (tarefa.coluna, tarefa.concluida_em, tarefa.coluna_desde) == ("feito", depois, depois)
    assert movimentos(conn, t)[1:] == [("a_fazer", "em_andamento", "comando"), ("em_andamento", "feito", "comando")]


def test_sair_de_feito_reabre(conn):
    t = criar_simples(conn)
    kanban.mover(conn, t, "feito", SEG)
    kanban.mover(conn, t, "a_fazer", SEG)
    assert kanban.obter(conn, t).concluida_em is None


def test_mover_para_mesma_coluna_nao_faz_nada(conn):
    t = criar_simples(conn)
    assert kanban.mover(conn, t, "a_fazer", SEG) is False
    assert len(movimentos(conn, t)) == 1


@pytest.mark.parametrize("coluna, erro", [("lixeira", "não existe"), ("feito", "#99")])
def test_mover_invalido(conn, coluna, erro):
    criar_simples(conn)
    alvo = 99 if coluna == "feito" else 1
    with pytest.raises(kanban.ErroQuadro, match=erro):
        kanban.mover(conn, alvo, coluna, SEG)


def test_apelidos_de_coluna():
    assert kanban.coluna_valida("Em Andamento") == "em_andamento"
    assert kanban.coluna_valida("concluída") == "feito"
    assert kanban.coluna_valida("A-Fazer") == "a_fazer"


# --- deduplicar ---------------------------------------------------------------------------------

def test_cria_tarefa_e_guarda_oportunidades_e_informativos(conn):
    t, o, i = email_(1), email_(2, "Novo projeto", "no-reply@freela.example.com"), email_(3, "Extrato")
    resumo = kanban.aplicar(conn, [t, o, i], [analise(t), analise(o, "oportunidade", titulo="Pentest", aderencia=9),
                                              analise(i, "informativo", titulo="Extrato")], SEG)
    assert resumo.criadas == [1]
    linhas = {l["message_id"]: (l["categoria"], l["tarefa_id"], l["aderencia"])
              for l in conn.execute("SELECT * FROM emails")}
    assert linhas == {t.message_id: ("tarefa", 1, 0), o.message_id: ("oportunidade", None, 9),
                      i.message_id: ("informativo", None, 0)}


def test_mesma_conversa_nao_duplica_e_sobe_prioridade(conn):
    primeiro = email_(1)
    kanban.aplicar(conn, [primeiro], [analise(primeiro, prioridade="baixa")], SEG)
    resposta = email_(2, "Re: Orçamento", conversa=primeiro.conversa)
    resumo = kanban.aplicar(conn, [resposta], [analise(resposta, prioridade="alta", titulo="Outro título",
                                                       prazo="2026-11-04")], SEG)
    assert resumo.criadas == [] and resumo.atualizadas == [1]
    tarefa = kanban.obter(conn, 1)
    assert (tarefa.titulo, tarefa.prioridade, tarefa.prazo) == ("Aprovar orçamento da Maria", "alta", "2026-11-04")
    assert conn.execute("SELECT COUNT(*) FROM tarefas").fetchone()[0] == 1


def test_mesmo_remetente_e_assunto_sem_prefixos_nao_duplica(conn):
    a, b = email_(1, "Orçamento do projeto"), email_(2, "RES: Orcamento do  projeto!")
    kanban.aplicar(conn, [a, b], [analise(a), analise(b)], SEG)
    assert conn.execute("SELECT COUNT(*) FROM tarefas").fetchone()[0] == 1


def test_mesmo_assunto_de_outra_pessoa_vira_outra_tarefa(conn):
    a, b = email_(1, "Orçamento"), email_(2, "Orçamento", remetente="joao@example.com")
    resumo = kanban.aplicar(conn, [a, b], [analise(a), analise(b)], SEG)
    assert resumo.criadas == [1, 2]


def test_tarefa_existente_apontada_pela_ia(conn):
    t = criar_simples(conn)
    e = email_(1, "Nota fiscal de outubro", remetente="contador@example.com")
    resumo = kanban.aplicar(conn, [e], [analise(e, tarefa_existente=t)], SEG)
    assert resumo.atualizadas == [t] and resumo.criadas == []


def test_ia_apontando_tarefa_feita_cria_nova(conn):
    t = criar_simples(conn)
    kanban.mover(conn, t, "feito", SEG)
    e = email_(1, remetente="contador@example.com")
    assert kanban.aplicar(conn, [e], [analise(e, tarefa_existente=t)], SEG).criadas == [2]


def test_novo_pedido_na_conversa_reabre_tarefa_feita(conn):
    primeiro = email_(1)
    kanban.aplicar(conn, [primeiro], [analise(primeiro)], SEG)
    kanban.mover(conn, 1, "feito", SEG)
    depois = email_(2, "Re: Orçamento", conversa=primeiro.conversa)
    resumo = kanban.aplicar(conn, [depois], [analise(depois)], SEG + timedelta(days=1))
    assert resumo.reabertas == [1] and kanban.obter(conn, 1).coluna == "a_fazer"
    assert movimentos(conn, 1)[-1] == ("feito", "a_fazer", "novo_email")


def test_informativo_na_conversa_feita_nao_reabre(conn):
    primeiro = email_(1)
    kanban.aplicar(conn, [primeiro], [analise(primeiro)], SEG)
    kanban.mover(conn, 1, "feito", SEG)
    obrigado = email_(2, "Re: Orçamento", conversa=primeiro.conversa)
    kanban.aplicar(conn, [obrigado], [analise(obrigado, "informativo", titulo="Agradecimento")], SEG)
    assert kanban.obter(conn, 1).coluna == "feito"


def test_separar_pula_vistos_e_anexa_conversas_abertas_sem_ia(conn):
    primeiro = email_(1)
    kanban.aplicar(conn, [primeiro], [analise(primeiro)], SEG)
    kanban.mover(conn, 1, "aguardando", SEG)
    resposta, outro = email_(2, "Re: Orçamento", conversa=primeiro.conversa), email_(3, "Reunião")
    novos, conhecidos, vistos = kanban.separar(conn, [primeiro, resposta, outro])
    assert (novos, conhecidos, vistos) == ([outro], [(resposta, 1)], 1)

    resumo = kanban.Resumo.vazio()
    kanban.anexar(conn, resposta, 1, SEG, resumo)
    # Aguardando + resposta chegou = volta para A Fazer.
    assert resumo.reabertas == [1] and kanban.obter(conn, 1).coluna == "a_fazer"
    assert kanban.ja_registrado(conn, "pessoal", resposta.message_id)


def test_vip_anexado_vira_alta(conn):
    primeiro = email_(1)
    kanban.aplicar(conn, [primeiro], [analise(primeiro, prioridade="baixa")], SEG)
    kanban.anexar(conn, email_(2, conversa=primeiro.conversa, vip=True), 1, SEG, kanban.Resumo.vazio())
    assert kanban.obter(conn, 1).prioridade == "alta"


# --- e-mails enviados ---------------------------------------------------------------------------

def enviado(n, conversa, responde_a="", quando=SEG):
    return Enviado(conta="pessoal", message_id=f"<s{n}@example.org>", conversa=conversa, responde_a=responde_a,
                   assunto="Re: Orçamento", data=quando)


def test_resposta_enviada_conclui_tarefa_responder(conn):
    e = email_(1)
    kanban.aplicar(conn, [e], [analise(e)], SEG)
    resumo = kanban.registrar_enviados(conn, [enviado(1, "<outra@x>", responde_a=e.message_id)], SEG)
    assert resumo.concluidas == [1]
    assert movimentos(conn, 1)[-1] == ("a_fazer", "feito", "email_enviado")


def test_enviado_antes_do_ultimo_email_nao_conclui(conn):
    e = email_(1)     # recebido às 07:00
    kanban.aplicar(conn, [e], [analise(e)], SEG)
    antes = enviado(1, e.conversa, quando=SEG - timedelta(hours=2))
    assert kanban.registrar_enviados(conn, [antes], SEG).concluidas == []


def test_enviado_nao_conclui_realizar_nem_aguardando(conn):
    a, b = email_(1), email_(2, "Fatura")
    kanban.aplicar(conn, [a, b], [analise(a, tipo="realizar"), analise(b)], SEG)
    kanban.mover(conn, 2, "aguardando", SEG)
    resumo = kanban.registrar_enviados(conn, [enviado(1, a.conversa), enviado(2, b.conversa)], SEG)
    assert resumo.concluidas == []


# --- atraso -------------------------------------------------------------------------------------

def test_dias_uteis_pula_domingo():
    uteis = {0, 1, 2, 3, 4, 5}
    assert kanban.dias_uteis_entre(date(2026, 11, 2), date(2026, 11, 5), uteis) == 3   # seg -> qui
    assert kanban.dias_uteis_entre(date(2026, 10, 31), date(2026, 11, 2), uteis) == 1  # sáb -> seg
    assert kanban.dias_uteis_entre(date(2026, 11, 2), date(2026, 11, 2), uteis) == 0


@pytest.mark.parametrize("dias, atrasada", [(3, False), (4, True)])
def test_atraso_depois_de_3_dias_uteis(conn, dias, atrasada):
    t = criar_simples(conn)                      # criada na segunda
    agora = SEG + timedelta(days=dias)           # qui = 3 dias úteis; sex = 4
    colunas = kanban.quadro(conn, agora, CFG, agora)
    tarefa = colunas["a_fazer"][0]
    assert (tarefa.id, tarefa.dias_parada, tarefa.atrasada) == (t, dias, atrasada)


def test_domingo_nao_conta_para_atraso(conn):
    criar_simples(conn, agora=SEG - timedelta(days=2))   # sábado
    quarta = SEG + timedelta(days=2)
    tarefa = kanban.quadro(conn, quarta, CFG, quarta)["a_fazer"][0]
    assert (tarefa.dias_parada, tarefa.atrasada) == (3, False)   # seg, ter, qua


def test_atraso_so_em_a_fazer_e_recomeca_ao_mover(conn):
    t = criar_simples(conn)
    sexta = SEG + timedelta(days=4)
    kanban.mover(conn, t, "em_andamento", sexta)
    tarefa = kanban.quadro(conn, sexta + timedelta(days=7), CFG, sexta)["em_andamento"][0]
    assert not tarefa.atrasada
    kanban.mover(conn, t, "a_fazer", sexta)
    assert not kanban.quadro(conn, sexta, CFG, sexta)["a_fazer"][0].atrasada


def test_prazo_vencido(conn):
    criar_simples(conn, prazo="2026-11-01")
    assert kanban.quadro(conn, SEG, CFG, SEG)["a_fazer"][0].prazo_vencido


# --- quadro e retrato ---------------------------------------------------------------------------

def test_quadro_ordena_e_feito_so_desde_o_ultimo_relatorio(conn):
    baixa = criar_simples(conn, prioridade="baixa")
    alta = criar_simples(conn, prioridade="alta")
    media_com_prazo = criar_simples(conn, prazo="2026-11-03")
    media = criar_simples(conn)
    velha, nova = criar_simples(conn), criar_simples(conn)
    kanban.mover(conn, velha, "feito", SEG - timedelta(days=2))
    kanban.mover(conn, nova, "feito", SEG + timedelta(hours=1))
    colunas = kanban.quadro(conn, SEG + timedelta(hours=2), CFG, feito_desde=SEG)
    assert [t.id for t in colunas["a_fazer"]] == [alta, media_com_prazo, media, baixa]
    assert [t.id for t in colunas["feito"]] == [nova]


def test_snapshot_por_dia(conn):
    criar_simples(conn)
    colunas = kanban.quadro(conn, SEG, CFG, SEG)
    kanban.salvar_snapshot(conn, SEG.date(), colunas)
    kanban.salvar_snapshot(conn, SEG.date(), colunas)   # mesmo dia substitui
    linhas = conn.execute("SELECT * FROM snapshots").fetchall()
    assert len(linhas) == 1 and json.loads(linhas[0]["quadro"])["a_fazer"][0]["titulo"] == "Emitir nota"


def test_para_ia_lista_so_abertas(conn):
    a, b = criar_simples(conn), criar_simples(conn)
    kanban.mover(conn, b, "feito", SEG)
    assert kanban.para_ia(conn) == [(a, "Emitir nota")]


# --- fluxo completo -----------------------------------------------------------------------------

def test_fluxo_grava_e_nao_reanalisa(conn):
    e1, e2 = email_(1), email_(2, "Re: Orçamento", conversa="<m1@example.com>")
    coleta = Coleta(emails=[e1], enviados=[enviado(1, e1.conversa, quando=SEG)])
    chamadas = []

    def analisar_falso(emails, cfg, contexto, prazo_limite=None):
        chamadas.append([e.uid for e in emails])
        return ResultadoAnalise(analises=[analise(e) for e in emails], tokens_entrada=100, tokens_saida=20)

    r = atualizar_quadro(CFG, conn, SEG - timedelta(days=1), SEG, coletar_fn=lambda *a: coleta,
                         analisar_fn=analisar_falso)
    assert r.resumo.criadas == [1] and r.resumo.concluidas == [1]

    # Segunda rodada: o e-mail já visto é pulado e a resposta da conversa é anexada sem IA.
    kanban.mover(conn, 1, "aguardando", SEG)
    coleta.emails.append(e2)
    r = atualizar_quadro(CFG, conn, SEG - timedelta(days=1), SEG, coletar_fn=lambda *a: coleta,
                         analisar_fn=analisar_falso)
    assert chamadas == [["1"]]
    assert (r.ja_vistos, r.anexados, r.para_ia, r.resumo.reabertas) == (1, 1, 0, [1])

    execucoes = conn.execute("SELECT status, tokens_entrada FROM execucoes ORDER BY id").fetchall()
    assert [tuple(x) for x in execucoes] == [("ok", 100), ("ok", 0)]


def test_fluxo_registra_falha(conn):
    def quebra(*a):
        raise RuntimeError("caiu")

    with pytest.raises(RuntimeError):
        atualizar_quadro(CFG, conn, SEG, SEG, coletar_fn=quebra)
    assert tuple(conn.execute("SELECT status, erro FROM execucoes").fetchone()) == ("falha", "RuntimeError: caiu")


# --- migração do banco --------------------------------------------------------------------------

def test_migra_banco_da_versao_1(tmp_path):
    caminho = tmp_path / "v1.db"
    antigo = sqlite3.connect(caminho)
    antigo.executescript(storage.SCHEMA.replace(
        "    categoria   TEXT,               -- tarefa | oportunidade | informativo (resultado da análise)\n"
        "    titulo      TEXT,               -- título dado pela análise (oportunidades e informativos)\n"
        "    aderencia   INTEGER NOT NULL DEFAULT 0,\n", ""))
    antigo.execute("PRAGMA user_version = 1")
    antigo.execute("INSERT INTO notas (texto, criada_em) VALUES ('mantida', '2026-10-30')")
    antigo.commit()
    antigo.close()

    conn = storage.conectar(caminho)
    colunas = {l["name"] for l in conn.execute("PRAGMA table_info(emails)")}
    assert {"categoria", "titulo", "aderencia"} <= colunas
    assert conn.execute("PRAGMA user_version").fetchone()[0] == storage.VERSAO_SCHEMA
    assert conn.execute("SELECT texto FROM notas").fetchone()[0] == "mantida"
    conn.close()
