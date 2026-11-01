"""Coleta e filtra e-mails das contas IMAP, sempre em modo somente leitura.

A caixa é aberta com readonly e as mensagens são lidas com BODY.PEEK, então
nada é marcado como lido. Cada e-mail relevante sai com o corpo limpo: sem
histórico citado, sem assinatura, com números longos mascarados e truncado.
"""

import email
import imaplib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

TIMEOUT = 30
# Em mensagens com anexo o texto vem antes dos anexos; o resto não é baixado.
LIMITE_BYTES = 300_000
LOTE_FETCH = 100
CATEGORIAS_GMAIL_IGNORADAS = "category:promotions OR category:social OR category:forums"
MESES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# "noreply" em qualquer parte do nome (noreply-pt@, jobalerts-noreply@) ou nomes típicos de sistema.
_REMETENTE_AUTOMATICO = re.compile(
    r"^([^@]*\b(no[-_.]?reply|do[-_.]?not[-_.]?reply)\b[^@]*"
    r"|mailer-daemon|postmaster|notifica[cç](ao|ão|oes|ões)|notifications?)@",
    re.IGNORECASE,
)
_INICIO_CITACAO = re.compile(
    # "Em qui., 30 de out. de 2026 às 09:00, Fulano <x@y> escreveu:" (pode quebrar linha);
    # o dígito logo no início evita cortar frases como "Em anexo segue...".
    r"^[ \t]*(Em [^\n]{0,40}\d[\s\S]{0,260}?escreveu:"
    r"|On [^\n]{0,40}\d[\s\S]{0,260}?wrote:"
    r"|-{2,}[ \t]*(Mensagem original|Original Message)[ \t]*-{2,}"
    r"|(De|From):[^\n]*\n[ \t]*(Enviad[oa]|Sent|Data|Date):)",
    re.IGNORECASE | re.MULTILINE,
)
_INICIO_ASSINATURA = re.compile(r"^(-- ?|Enviado do meu .*|Sent from my .*)$", re.MULTILINE)
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
# Números com . ou - entre os dígitos; datas com / ficam de fora de propósito.
_NUMERO = re.compile(r"(?<![\w/])\d(?:[\d.\-]*\d)?(?![\w/])")
MIN_DIGITOS_MASCARA = 9


@dataclass
class Email:
    conta: str
    uid: str
    message_id: str
    conversa: str        # Message-ID da primeira mensagem da conversa
    remetente_nome: str
    remetente: str
    assunto: str
    data: datetime
    corpo: str
    vip: bool = False


@dataclass
class Descartado:
    conta: str
    remetente: str
    assunto: str
    data: datetime
    motivo: str


@dataclass
class Enviado:
    """E-mail que você enviou: se responde uma tarefa "Responder", ela vai para Feito."""
    conta: str
    message_id: str
    conversa: str
    responde_a: str      # In-Reply-To
    assunto: str
    data: datetime


@dataclass
class Coleta:
    emails: list[Email] = field(default_factory=list)
    descartados: list[Descartado] = field(default_factory=list)
    enviados: list[Enviado] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


def coletar(cfg: dict, inicio: datetime, fim: datetime) -> Coleta:
    """Lê todas as contas; uma conta com erro não impede as outras."""
    coleta = Coleta()
    for conta in cfg["contas"]:
        nome = conta["nome"]
        if conta["tipo"] != "imap":
            coleta.erros.append(f"[{nome}] tipo {conta['tipo']!r} ainda não implementado")
            continue
        endereco = os.environ.get(conta["endereco_env"])
        senha = os.environ.get(conta["senha_env"])
        if not endereco or not senha:
            coleta.erros.append(f"[{nome}] {conta['endereco_env']} e {conta['senha_env']} precisam estar no .env")
            continue
        try:
            coletar_conta(conta, endereco, senha, inicio, fim, cfg, coleta)
        except (imaplib.IMAP4.error, OSError) as e:
            coleta.erros.append(f"[{nome}] {e}")
    coleta.emails.sort(key=lambda e: e.data)
    coleta.enviados.sort(key=lambda e: e.data)
    return coleta


def coletar_conta(conta: dict, endereco: str, senha: str, inicio: datetime, fim: datetime,
                  cfg: dict, coleta: Coleta, conectar=None) -> None:
    filtros = dict(cfg.get("filtros") or {})
    # Sites de oportunidades também nunca são filtrados.
    fontes = (cfg.get("oportunidades") or {}).get("fontes", [])
    filtros["incluir_sempre"] = list(filtros.get("incluir_sempre", [])) + list(fontes)
    max_chars = cfg["privacidade"]["max_caracteres_corpo"]

    imap = (conectar or imaplib.IMAP4_SSL)(conta["servidor"], conta["porta"], timeout=TIMEOUT)
    try:
        imap.login(endereco, senha)
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error("não foi possível abrir a caixa de entrada")

        # SINCE só compara a data (sem hora); a hora exata é conferida pelo INTERNALDATE.
        desde = data_imap(inicio)
        uids = buscar(imap, "SINCE", desde)
        promocoes = set()
        if "X-GM-EXT-1" in imap.capabilities:
            promocoes = set(buscar(imap, "X-GM-RAW", f'"{CATEGORIAS_GMAIL_IGNORADAS}"', "SINCE", desde))

        manter = {}
        for uid, recebido, bruto in baixar(imap, uids, "(INTERNALDATE BODY.PEEK[HEADER])"):
            if not inicio <= recebido < fim:
                continue
            cabecalho = email.message_from_bytes(bruto, policy=policy.default)
            nome, remetente = remetente_de(cabecalho)
            assunto = assunto_de(cabecalho)
            motivo = motivo_descarte(cabecalho, remetente, endereco, filtros, uid in promocoes)
            if motivo:
                coleta.descartados.append(Descartado(conta["nome"], remetente, assunto, recebido, motivo))
            else:
                manter[uid] = recebido

        for uid, _, bruto in baixar(imap, list(manter), f"(BODY.PEEK[]<0.{LIMITE_BYTES}>)"):
            msg = email.message_from_bytes(bruto, policy=policy.default)
            coleta.emails.append(montar_email(msg, conta["nome"], uid, manter[uid], filtros, max_chars))

        # Enviados: um erro aqui não descarta a caixa de entrada já lida.
        try:
            coletar_enviados(imap, conta["nome"], inicio, fim, coleta)
        except (imaplib.IMAP4.error, OSError) as e:
            coleta.erros.append(f"[{conta['nome']}] pasta de enviados: {e}")
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def buscar_email(cfg: dict, nome_conta: str, message_id: str, conectar=None) -> Email | None:
    """Relê um e-mail pelo Message-ID (o corpo não fica no banco), para gerar o rascunho sob demanda."""
    conta = next((c for c in cfg["contas"] if c["nome"] == nome_conta), None)
    if conta is None or conta["tipo"] != "imap":
        return None
    endereco, senha = os.environ.get(conta["endereco_env"]), os.environ.get(conta["senha_env"])
    if not endereco or not senha:
        return None
    filtros = cfg.get("filtros") or {}
    imap = (conectar or imaplib.IMAP4_SSL)(conta["servidor"], conta["porta"], timeout=TIMEOUT)
    try:
        imap.login(endereco, senha)
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error("não foi possível abrir a caixa de entrada")
        # Só o Message-ID entre aspas: sem espaços nem aspas internas, não dá para injetar critérios.
        alvo = message_id.replace('"', "").replace("\\", "").strip()
        uids = buscar(imap, "HEADER", "Message-ID", f'"{alvo}"')
        for uid, recebido, bruto in baixar(imap, uids[-1:], f"(INTERNALDATE BODY.PEEK[]<0.{LIMITE_BYTES}>)"):
            msg = email.message_from_bytes(bruto, policy=policy.default)
            return montar_email(msg, nome_conta, uid, recebido, filtros, cfg["privacidade"]["max_caracteres_corpo"])
        return None
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


_LINHA_LIST = re.compile(rb'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<nome>.+)$')


def pasta_enviados(imap) -> str | None:
    """Pasta marcada como \\Sent no LIST (no Gmail em português: "[Gmail]/E-mails enviados")."""
    status, linhas = imap.list()
    if status != "OK":
        return None
    for linha in linhas or []:
        if isinstance(linha, tuple):  # nome em literal: raro, ignora
            continue
        achado = _LINHA_LIST.match(linha or b"")
        if achado and b"\\sent" in achado.group("flags").lower():
            # Devolve como veio (com aspas, se houver), pronto para o SELECT.
            return achado.group("nome").decode()
    return None


def coletar_enviados(imap, nome_conta: str, inicio: datetime, fim: datetime, coleta: Coleta) -> None:
    pasta = pasta_enviados(imap)
    if pasta is None:
        return
    status, _ = imap.select(pasta, readonly=True)
    if status != "OK":
        raise imaplib.IMAP4.error(f"não foi possível abrir {pasta}")
    uids = buscar(imap, "SINCE", data_imap(inicio))
    campos = "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES SUBJECT)])"
    for uid, enviado_em, bruto in baixar(imap, uids, campos):
        if not inicio <= enviado_em < fim:
            continue
        msg = email.message_from_bytes(bruto, policy=policy.default)
        message_id = str(msg.get("Message-ID", "")).strip() or f"<enviado-{uid}@{nome_conta}>"
        coleta.enviados.append(Enviado(
            conta=nome_conta,
            message_id=message_id,
            conversa=raiz_conversa(msg, message_id),
            responde_a=str(msg.get("In-Reply-To", "")).strip(),
            assunto=assunto_de(msg),
            data=enviado_em,
        ))


def buscar(imap, *criterios: str) -> list[str]:
    status, dados = imap.uid("SEARCH", *criterios)
    if status != "OK":
        raise imaplib.IMAP4.error(f"busca recusada: {criterios}")
    return dados[0].decode().split() if dados and dados[0] else []


def baixar(imap, uids: list[str], partes: str):
    """Gera (uid, INTERNALDATE, conteúdo) para cada mensagem, em lotes."""
    for i in range(0, len(uids), LOTE_FETCH):
        status, dados = imap.uid("FETCH", ",".join(uids[i:i + LOTE_FETCH]), partes)
        if status != "OK":
            raise imaplib.IMAP4.error("falha ao baixar mensagens")
        for item in dados:
            # A resposta alterna tuplas (metadados, conteúdo) com fechamentos b")".
            if not isinstance(item, tuple):
                continue
            meta, conteudo = item
            uid = re.search(rb"UID (\d+)", meta)
            interno = re.search(rb'INTERNALDATE "([^"]+)"', meta)
            yield (
                uid.group(1).decode() if uid else "",
                ler_internaldate(interno.group(1).decode()) if interno else datetime.now(timezone.utc),
                conteudo,
            )


def data_imap(quando: datetime) -> str:
    return f"{quando.day:02d}-{MESES[quando.month - 1]}-{quando.year}"


def ler_internaldate(texto: str) -> datetime:
    """Converte '30-Oct-2026 09:12:00 -0300' sem depender do locale."""
    data, hora, fuso = texto.split()
    dia, mes, ano = data.split("-")
    h, m, s = map(int, hora.split(":"))
    sinal = -1 if fuso[0] == "-" else 1
    deslocamento = timedelta(hours=int(fuso[1:3]), minutes=int(fuso[3:5])) * sinal
    return datetime(int(ano), MESES.index(mes) + 1, int(dia), h, m, s, tzinfo=timezone(deslocamento))


def remetente_de(msg: EmailMessage) -> tuple[str, str]:
    nome, endereco = parseaddr(str(msg.get("From", "")))
    return nome, endereco.lower()


def assunto_de(msg: EmailMessage) -> str:
    assunto = " ".join(str(msg.get("Subject", "")).split())
    return assunto or "(sem assunto)"


_PREFIXOS_RESPOSTA = re.compile(r"^((re|res|fw|fwd|enc|tr|rv)\s*:\s*)+", re.IGNORECASE)


def assunto_sem_prefixos(assunto: str) -> str:
    """'Re: RES: Fwd: Orçamento' -> 'Orçamento'."""
    return _PREFIXOS_RESPOSTA.sub("", assunto).strip() or assunto


def e_automatico(remetente: str) -> bool:
    return bool(_REMETENTE_AUTOMATICO.match(remetente))


def corresponde(endereco: str, padroes: list[str]) -> bool:
    """'x@loja.com' compara o endereço; '@loja.com' compara o domínio e seus subdomínios."""
    endereco = endereco.lower()
    dominio = endereco.rpartition("@")[2]
    for padrao in padroes:
        padrao = padrao.lower().strip()
        if padrao.startswith("@"):
            alvo = padrao[1:]
            if dominio == alvo or dominio.endswith("." + alvo):
                return True
        elif endereco == padrao:
            return True
    return False


def motivo_descarte(msg: EmailMessage, remetente: str, proprio_endereco: str,
                    filtros: dict, e_promocao: bool) -> str | None:
    """Motivo para ignorar o e-mail, ou None se ele é relevante."""
    if corresponde(remetente, filtros.get("excluir_remetentes", [])):
        return "excluído"
    if corresponde(remetente, filtros.get("incluir_sempre", []) + filtros.get("vip", [])):
        return None
    if remetente == proprio_endereco.lower():
        return "enviado por você"
    if str(msg.get("X-Spam-Flag", "")).strip().upper() == "YES":
        return "spam"
    if e_promocao:
        return "promoção"
    if str(msg.get("Auto-Submitted", "no")).strip().lower() != "no" or e_automatico(remetente):
        return "automático"
    precedencia = str(msg.get("Precedence", "")).strip().lower()
    if msg.get("List-Unsubscribe") or msg.get("List-Id") or precedencia in ("bulk", "list", "junk"):
        return "newsletter"
    return None


def montar_email(msg: EmailMessage, conta: str, uid: str, recebido: datetime,
                 filtros: dict, max_chars: int) -> Email:
    nome, remetente = remetente_de(msg)
    message_id = str(msg.get("Message-ID", "")).strip() or f"<sem-id-{uid}@{conta}>"
    return Email(
        conta=conta,
        uid=uid,
        message_id=message_id,
        conversa=raiz_conversa(msg, message_id),
        remetente_nome=nome,
        remetente=remetente,
        assunto=assunto_de(msg),
        data=recebido,
        corpo=limpar_corpo(recortar_aviso(extrair_texto(msg), remetente), max_chars),
        vip=corresponde(remetente, filtros.get("vip", [])),
    )


def raiz_conversa(msg: EmailMessage, message_id: str) -> str:
    referencias = str(msg.get("References", "")).split()
    if referencias:
        return referencias[0]
    return str(msg.get("In-Reply-To", "")).strip() or message_id


def extrair_texto(msg: EmailMessage) -> str:
    parte = msg.get_body(preferencelist=("plain", "html"))
    if parte is None:
        return ""
    try:
        conteudo = parte.get_content()
    except (LookupError, UnicodeDecodeError):  # charset desconhecido ou mal declarado
        conteudo = (parte.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
    if parte.get_content_subtype() == "html":
        conteudo = html_para_texto(conteudo)
    return conteudo


class _ConversorHtml(HTMLParser):
    BLOCOS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__()
        self.partes = []
        self.tag_pulada = None
        self.profundidade = 0

    def handle_starttag(self, tag, attrs):
        if self.tag_pulada:
            if tag == self.tag_pulada:
                self.profundidade += 1
            return
        classes = dict(attrs).get("class") or ""
        # Histórico citado (blockquote, gmail_quote) e código não interessam.
        if tag in ("script", "style", "head", "blockquote") or "gmail_quote" in classes:
            self.tag_pulada, self.profundidade = tag, 1
        elif tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if self.tag_pulada:
            if tag == self.tag_pulada:
                self.profundidade -= 1
                if self.profundidade == 0:
                    self.tag_pulada = None
        elif tag in self.BLOCOS:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self.tag_pulada:
            self.partes.append(data)


def html_para_texto(html: str) -> str:
    conversor = _ConversorHtml()
    conversor.feed(html)
    conversor.close()
    return "".join(conversor.partes)


# Avisos de projeto dos sites de freelancer: só o trecho do projeto vai para a IA. A saudação com
# o nome, os links e o rodapé não ajudam a classificar e custam tempo no modelo local.
_RECORTES = {
    "99freelas.com.br": (re.compile(r"novo projeto que pode ser do seu interesse:", re.I),
                         re.compile(r"^\s*\*?\s*Este é um e-?mail automático", re.I | re.M)),
}
_LINHAS_RUIDO = re.compile(r"^\s*(Leia mais\.?|Enviar Proposta)\s*$", re.I | re.M)


def recortar_aviso(texto: str, remetente: str) -> str:
    dominio = remetente.rsplit("@", 1)[-1].lower()
    for chave, (inicio, fim) in _RECORTES.items():
        if dominio != chave and not dominio.endswith("." + chave):
            continue
        achados = list(inicio.finditer(texto))
        if not achados:      # formato mudou: manda o e-mail inteiro, como antes
            return texto
        trecho = texto[achados[-1].end():]
        corte = fim.search(trecho)
        if corte:
            trecho = trecho[:corte.start()]
        return _LINHAS_RUIDO.sub("", trecho)
    return texto


def limpar_corpo(texto: str, max_chars: int) -> str:
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    for padrao in (_INICIO_CITACAO, _INICIO_ASSINATURA):
        corte = padrao.search(texto)
        if corte:
            texto = texto[:corte.start()]
    linhas = [" ".join(linha.split()) for linha in texto.split("\n") if not linha.lstrip().startswith(">")]
    texto = re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()
    texto = mascarar_numeros(texto)
    if len(texto) > max_chars:
        texto = texto[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"
    return texto


def mascarar_numeros(texto: str) -> str:
    """Esconde CPF, CNPJ, contas, cartões e telefones (9+ dígitos); preserva datas e valores."""
    texto = _CNPJ.sub("[número]", texto)

    def trocar(m: re.Match) -> str:
        digitos = sum(c.isdigit() for c in m.group())
        return "[número]" if digitos >= MIN_DIGITOS_MASCARA else m.group()

    return _NUMERO.sub(trocar, texto)
