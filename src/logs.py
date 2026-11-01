"""Logs do serviço: terminal (docker logs) e arquivo com rotação em logs/assistente.log.

Nada de conteúdo de e-mail vai para o log: só contagens, tempos, tokens e erros.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

TAMANHO_MAXIMO = 1_000_000   # 1 MB por arquivo
ARQUIVOS_ANTIGOS = 5         # assistente.log.1 ... .5


def configurar_arquivo(pasta: Path) -> Path | None:
    """Liga o log em arquivo; sem permissão na pasta, segue só com o terminal (docker logs)."""
    # O agendador registra cada execução de cada tarefa (inclusive o batimento a cada 5 min).
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    caminho = pasta / "assistente.log"
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = RotatingFileHandler(caminho, maxBytes=TAMANHO_MAXIMO, backupCount=ARQUIVOS_ANTIGOS,
                                      encoding="utf-8")
    except OSError as e:
        logging.getLogger("assistente").warning(
            "sem log em arquivo (%s); no servidor rode: sudo chown -R 1000:1000 logs", e)
        return None
    arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(arquivo)
    return caminho
