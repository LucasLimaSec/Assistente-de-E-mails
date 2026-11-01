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

- **Custo mínimo**: por padrão a análise roda num modelo pequeno no próprio servidor (Ollama), sem custo. Quando quiser, o Claude Haiku 5.5 pela API entra trocando uma linha do config, na casa dos centavos de dólar por mês.
- **Leitura rápida**: o relatório se lê em menos de um minuto no celular.
- **Somente leitura**: o sistema nunca envia nem apaga e-mails.

## Como funciona

1. Às 08:00, de segunda a sábado, o sistema coleta os e-mails recebidos desde o último relatório, ignorando spam e promoções.
2. Carrega o quadro salvo e o que você marcou como feito, seja pelo Telegram ou por respostas enviadas.
3. Envia os e-mails resumidos à IA configurada, que devolve tarefas novas e prioridades. Os rascunhos de resposta são gerados quando você pede.
4. Atualiza o quadro no banco SQLite e manda o relatório pelo bot do Telegram.
5. Durante o dia, o bot recebe comandos como `/feito 12`, `/nova Ligar para o contador` e `/rascunho 12`.

Aos sábados, às 17:00, chega também um resumo semanal.

## Requisitos

- Servidor Linux ligado 24 h com [Docker](https://docs.docker.com/engine/install/) e Docker Compose
- Opcional: chave da [API do Claude](https://platform.claude.com/) (Claude Console), só se for usar `llm.provider: claude`
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
| `ANTHROPIC_API_KEY` | Opcional. Claude Console > API Keys. Defina também um limite de gasto. |
| `TELEGRAM_BOT_TOKEN` | @BotFather > `/newbot` |
| `TELEGRAM_CHAT_ID` | Mande `/start` ao seu bot e abra `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `GMAIL_PESSOAL_ENDERECO` | Seu endereço Gmail |
| `GMAIL_PESSOAL_SENHA_APP` | Conta Google > Segurança > Verificação em duas etapas > Senhas de app |
| `GOOGLE_AGENDA_ICAL_URL` | Opcional. Google Agenda > Configurações da agenda > "Endereço secreto no formato iCal" |
| `PERFIL_OPORTUNIDADES` | Seu perfil profissional em uma linha (áreas e tecnologias), usado para escolher as oportunidades de projetos |

### 3. Preencher o `.env`

```bash
cp .env.example .env
nano .env
```

O `.env` está no `.gitignore` e nunca deve ser enviado ao repositório.

### 4. Subir os containers e baixar o modelo de IA

```bash
mkdir -p data        # crie antes, para a pasta não pertencer ao root
docker compose build
docker compose up -d ollama
docker compose exec ollama ollama pull qwen3.5:4b    # ~3–4 GB, uma vez só
docker compose run --rm assistente-email
```

O último comando executa `python main.py --check` e encerra.

A saída esperada é:

```
[ OK ] Config: config.yaml válido
[ OK ] Banco: /app/data/assistente.db (6 tabelas: ...)
[ OK ] IA principal (ollama): modelo qwen3.5:4b disponível em http://ollama:11434
[ OK ] IA reserva (regras): sem IA, sempre disponível
[ OK ] Perfil: N caracteres
[ OK ] Telegram: bot @seu_bot acessa o chat de ...
[ OK ] E-mail [pessoal]: ... conectado, N mensagens na caixa de entrada
```

Cada linha `[FALHA]` diz qual chave ou conexão corrigir.

> Se o UID do seu usuário no servidor não for 1000, construa a imagem com `docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)`.

### 5. Conferir os e-mails coletados

Lista os e-mails que entrariam no próximo relatório, já filtrados, sem marcar nada como lido:

```bash
docker compose run --rm assistente-email python main.py --emails
```

| Opção | Efeito |
| --- | --- |
| `--horas 24` | Usa as últimas N horas em vez da janela do relatório (desde o relatório do último dia útil) |
| `--descartados` | Mostra também os e-mails ignorados e o motivo (`promoção`, `newsletter`, `automático`, `excluído`...) |

Se algum e-mail importante aparecer como descartado, adicione o remetente em `filtros.incluir_sempre`. Se algo irrelevante passar, use `filtros.excluir_remetentes`.

### 6. Medir e testar a IA

```bash
# Mede tempo, memória e acertos com 10 e-mails fictícios (não usa seus e-mails)
docker compose run --rm assistente-email python main.py --benchmark-ia

# Analisa seus e-mails reais e mostra tarefas, oportunidades e informativos (não grava nada)
docker compose run --rm assistente-email python main.py --analisar --horas 24
```

Com `--provedor regras` ou `--provedor claude` dá para comparar com os outros provedores. Se o benchmark mostrar que o servidor é lento demais, troque `llm.ollama.model` por `qwen3.5:2b` ou diminua `llm.lote_emails`.

Para ver exatamente o texto que a IA recebe de cada e-mail, use `--emails --corpo`.

### 7. Montar o quadro Kanban

```bash
# Coleta, analisa e GRAVA no quadro. Rodar de novo não reanalisa os e-mails já processados.
docker compose run --rm assistente-email python main.py --atualizar-quadro --horas 24

# Mostra o quadro e move tarefas (as mesmas ações do /feito no Telegram, que chega na Fase 6)
docker compose run --rm assistente-email python main.py --quadro
docker compose run --rm assistente-email python main.py --mover 3 feito
```

Regras do quadro:

- **Colunas**: A Fazer, Em Andamento, Aguardando e Feito. Cada tarefa tem um número fixo.
- **Sem duplicar**: e-mail da mesma conversa, ou do mesmo remetente com o mesmo assunto (sem `Re:`/`Fwd:`), atualiza a tarefa existente. Conversa com tarefa aberta nem passa pela IA.
- **Resposta enviada**: se você responde a conversa de uma tarefa "Responder", ela vai sozinha para Feito (lido da pasta de enviados, também só leitura).
- **Volta para A Fazer**: tarefa em Aguardando ou Feito que recebe e-mail novo na conversa.
- **Atraso**: ⚠️ em tarefa parada em A Fazer há mais de `kanban.dias_para_atraso` dias úteis.

### 8. Receber o relatório no Telegram

```bash
# Mostra o relatório no terminal sem enviar nem gravar nada (roda numa cópia do banco em memória)
docker compose run --rm assistente-email python main.py --relatorio --dry-run

# Fluxo completo: coleta, análise, quadro e envio ao Telegram
docker compose run --rm assistente-email python main.py --relatorio
```

O relatório traz o resumo no topo, o que foi feito desde o último relatório, as colunas do quadro (até `kanban.max_itens_por_coluna` itens cada, o resto como "+N"), as oportunidades por aderência, o foco do dia e avisos de falha. Na Fase 7 ele passa a sair sozinho às 08:00.

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
| `oportunidades` | Sites de projetos (`fontes`) cujos e-mails vão para a seção de oportunidades em vez do quadro |
| `llm` | Provedor de IA (`ollama`, `regras` ou `claude`), reserva, horário da análise, tamanho do lote, modelo e domínios que só podem ir à IA local |
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
├── main.py              # ponto de entrada (--check, --emails, --analisar, --atualizar-quadro, --quadro...)
├── config.yaml          # preferências
├── .env.example         # modelo das variáveis secretas
├── Dockerfile
├── docker-compose.yml
├── src/
│   ├── ai_analyzer.py   # lotes, novas tentativas, reserva e regras do config sobre a IA
│   ├── benchmark.py     # --benchmark-ia
│   ├── config.py        # leitura e validação do config.yaml
│   ├── email_reader.py  # coleta IMAP (entrada e enviados), filtros e limpeza do corpo
│   ├── fluxo.py         # coleta → análise → quadro
│   ├── janela.py        # período coberto por cada relatório
│   ├── kanban.py        # regras do quadro: mover, deduplicar, atraso, retrato do dia
│   ├── report.py        # monta a mensagem do relatório (HTML do Telegram)
│   ├── llm/             # provedores de IA: regras, ollama e claude
│   ├── storage.py       # schema e conexão SQLite
│   ├── telegram_bot.py  # envio pelo bot
│   └── verificacao.py   # testes de chaves e conexões
├── prompts/             # prompts da IA, editáveis sem mexer no código
└── tests/               # pytest e e-mails fictícios (tests/fixtures)
```

## Privacidade e segurança

- O acesso ao e-mail é somente leitura.
- Por padrão a IA roda no próprio servidor e nenhum e-mail sai dele.
- À IA vai apenas o corpo resumido de cada e-mail (até ~1.500 caracteres), sem anexos, assinaturas, histórico citado nem números longos como CPF, contas e telefones. Se usar a API do Claude, ela [não usa esses dados para treinar modelos](https://privacy.claude.com/en/articles/7996866).
- Domínios listados em `llm.local_only_domains` só são analisados por uma IA local (Ollama) e não saem do servidor.
- O bot responde apenas ao `TELEGRAM_CHAT_ID` configurado.
- Endereços, senhas e tokens ficam só no `.env`, fora do repositório.

## Roteiro

- [x] **Fase 1 – Base**: estrutura, config, banco SQLite, container e `--check`
- [x] **Fase 2 – E-mail**: coleta e filtros
- [x] **Fase 3 – IA**: provedores `ollama`, `regras` e `claude`, benchmark no servidor e registro de tokens
- [x] **Fase 4 – Kanban**: mover, deduplicar e marcar atraso
- [ ] **Fase 5 – Telegram (envio)**: relatório diário
- [ ] **Fase 6 – Telegram (comandos)**: `/feito`, `/nova`, `/quadro`, `/rascunho`, notas, agenda e resumo semanal
- [ ] **Fase 7 – Agendamento**: execução automática, logs e aviso de falha
- [ ] **Fase 8 – Extras**: IA no PC de mesa e resumo em áudio

## Licença

Distribuído sob a licença MIT. Veja [`LICENSE`](LICENSE).
