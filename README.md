# Assistente de E-mails

Uma secretária no Telegram: todo dia de manhã ela lê seus e-mails, cruza com o que você fez no dia anterior e envia um quadro Kanban curto dizendo o que responder, o que realizar e o que ficou pendente.

> **Status:** em desenvolvimento. A Fase 1 (base, banco e verificação das chaves) está pronta; as demais estão no [roteiro](#roteiro).

## Sumário

- [Introdução](#introdução)
- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Guia de uso](#guia-de-uso)
- [Configuração](#configuração)
- [Desenvolvimento](#desenvolvimento)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Privacidade e segurança](#privacidade-e-segurança)
- [Roteiro](#roteiro)
- [Licença](#licença)

## Introdução

Caixas de entrada misturam pedidos, avisos e propaganda. O Assistente de E-mails separa o que importa e transforma cada e-mail relevante em uma tarefa com tipo (Responder ou Realizar) e prioridade. O quadro tem memória entre os dias: tarefas concluídas saem, as paradas ganham marca de atraso, e você move tudo por comandos no próprio Telegram.

Exemplo do relatório:

```
☀️ Bom dia! Quarta, 28/10
3 para responder · 2 para realizar · 1 atrasada

✅ FEITO ONTEM
• Respondeu João sobre o contrato

📋 A FAZER
🔴 #12 Responder: Maria – aprovar orçamento (hoje)
🟡 #14 Realizar: Emitir nota fiscal do cliente X
⚠️ #9 Ligar para o contador (há 4 dias)

🔄 EM ANDAMENTO
• #7 Proposta para empresa Y

⏳ AGUARDANDO
• #5 Retorno do fornecedor sobre prazo

💡 Foco do dia: aprovar o orçamento da Maria antes das 12h.
```

Princípios:

- **Custo mínimo**: a análise usa o Claude Haiku 5.5 pela API, na casa dos centavos de dólar por mês.
- **Leitura rápida**: o relatório se lê em menos de um minuto no celular.
- **Somente leitura**: o sistema nunca envia nem apaga e-mails.

## Como funciona

1. Às 08:00, de segunda a sábado, o sistema coleta os e-mails recebidos desde o último relatório, ignorando spam e promoções.
2. Carrega o quadro salvo e o que você marcou como feito, seja pelo Telegram ou por respostas enviadas.
3. Envia os e-mails resumidos ao Claude, que devolve tarefas novas, prioridades e rascunhos de resposta.
4. Atualiza o quadro no banco SQLite e manda o relatório pelo bot do Telegram.
5. Durante o dia, o bot recebe comandos como `/feito 12`, `/nova Ligar para o contador` e `/rascunho 12`.

Aos sábados, às 17:00, chega também um resumo semanal.

## Requisitos

- Servidor Linux ligado 24 h com [Docker](https://docs.docker.com/engine/install/) e Docker Compose
- Chave da [API do Claude](https://platform.claude.com/) (Claude Console)
- Um bot do Telegram, criado com o [@BotFather](https://t.me/BotFather)
- Conta Gmail com verificação em duas etapas e uma [senha de app](https://myaccount.google.com/apppasswords)

## Guia de uso

### 1. Clonar o repositório

```bash
git clone https://github.com/LucasLimaSec/Assistente-de-E-mails.git
cd Assistente-de-E-mails
```

### 2. Criar as credenciais

| Credencial | Onde obter |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude Console > API Keys. Defina também um limite de gasto. |
| `TELEGRAM_BOT_TOKEN` | @BotFather > `/newbot` |
| `TELEGRAM_CHAT_ID` | Mande `/start` ao seu bot e abra `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `GMAIL_PESSOAL_ENDERECO` | Seu endereço Gmail |
| `GMAIL_PESSOAL_SENHA_APP` | Conta Google > Segurança > Verificação em duas etapas > Senhas de app |
| `GOOGLE_AGENDA_ICAL_URL` | Opcional. Google Agenda > Configurações da agenda > "Endereço secreto no formato iCal" |

### 3. Preencher o `.env`

```bash
cp .env.example .env
nano .env
```

O `.env` está no `.gitignore` e nunca deve ser enviado ao repositório.

### 4. Subir o container

```bash
mkdir -p data        # crie antes, para a pasta não pertencer ao root
docker compose up --build
```

Nesta fase, o container executa `python main.py --check` e encerra. A saída esperada é:

```
[ OK ] Config: config.yaml válido
[ OK ] Banco: /app/data/assistente.db (6 tabelas: ...)
[ OK ] Claude API: chave válida, modelo claude-haiku-5-5 disponível
[ OK ] Telegram: bot @seu_bot acessa o chat de ...
[ OK ] E-mail [pessoal]: ... conectado, N mensagens na caixa de entrada
```

Cada linha `[FALHA]` diz qual chave ou conexão corrigir.

> Se o UID do seu usuário no servidor não for 1000, construa a imagem com `docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)`.

## Configuração

As preferências ficam no [`config.yaml`](config.yaml); os segredos, só no `.env`.

| Seção | O que define |
| --- | --- |
| `geral` | Fuso horário, horário do relatório, dias úteis e caminho do banco |
| `contas` | Contas de e-mail lidas. Endereço e senha são referenciados por nome de variável do `.env` (`endereco_env`, `senha_env`) |
| `filtros` | `excluir_remetentes`, `incluir_sempre` e `vip`, com endereços (`x@loja.com`) ou domínios (`@loja.com`) |
| `kanban` | Dias úteis até marcar atraso e máximo de itens por coluna |
| `resumo_semanal` | Dia e horário do resumo da semana |
| `agenda` | Liga a leitura do Google Agenda |
| `llm` | Provedor de IA (`claude` ou `ollama`), modelo, reserva e domínios que só podem ir à IA local |
| `audio` | Envia o resumo também como mensagem de voz |

Para adicionar outra conta, copie o bloco em `contas` com um `nome` novo e crie as variáveis correspondentes no `.env`.

## Desenvolvimento

Para rodar fora do container (Python 3.12):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python main.py --check
.venv/bin/python -m pytest
```

O plano completo, as decisões e as convenções do projeto estão no [`CLAUDE.md`](CLAUDE.md).

## Estrutura do projeto

```
├── main.py              # ponto de entrada (--check)
├── config.yaml          # preferências
├── .env.example         # modelo das variáveis secretas
├── Dockerfile
├── docker-compose.yml
├── src/
│   ├── config.py        # leitura e validação do config.yaml
│   ├── storage.py       # schema e conexão SQLite
│   └── verificacao.py   # testes de chaves e conexões
└── tests/               # pytest
```

## Privacidade e segurança

- O acesso ao e-mail é somente leitura.
- Ao Claude vai apenas o corpo resumido de cada e-mail (até ~1.500 caracteres), sem anexos, assinaturas nem números longos como CPF e contas. A API do Claude [não usa esses dados para treinar modelos](https://privacy.claude.com/en/articles/7996866).
- Domínios listados em `llm.local_only_domains` só são analisados por uma IA local (Ollama) e não saem do servidor.
- O bot responde apenas ao `TELEGRAM_CHAT_ID` configurado.
- Endereços, senhas e tokens ficam só no `.env`, fora do repositório.

## Roteiro

- [x] **Fase 1 – Base**: estrutura, config, banco SQLite, container e `--check`
- [ ] **Fase 2 – E-mail**: coleta e filtros
- [ ] **Fase 3 – IA**: análise com o Claude e registro de tokens
- [ ] **Fase 4 – Kanban**: mover, deduplicar e marcar atraso
- [ ] **Fase 5 – Telegram (envio)**: relatório diário
- [ ] **Fase 6 – Telegram (comandos)**: `/feito`, `/nova`, `/quadro`, `/rascunho`, notas, agenda e resumo semanal
- [ ] **Fase 7 – Agendamento**: execução automática, logs e aviso de falha
- [ ] **Fase 8 – Extras**: IA local (Ollama) e resumo em áudio

## Licença

Distribuído sob a licença MIT. Veja [`LICENSE`](LICENSE).
