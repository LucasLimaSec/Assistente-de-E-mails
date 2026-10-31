FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo

# Mesmo UID do usuário do servidor, para o volume data/ ficar com o dono certo.
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} app && useradd -u ${UID} -g ${GID} -m app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py config.yaml ./
COPY src/ src/
COPY prompts/ prompts/
# Só os e-mails fictícios, usados pelo --benchmark-ia.
COPY tests/fixtures/ tests/fixtures/

RUN mkdir -p data && chown app:app data
USER app

# Por enquanto só a verificação. Na Fase 6 passa a rodar o bot permanente.
CMD ["python", "main.py", "--check"]
