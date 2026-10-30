# Plano: Assistente de E-mails (Claude Code)

Oct 28, 2026

## 1. Objetivo

Todo dia de manhã, um script lê os e-mails das últimas 24 h, cruza com o que foi feito no dia anterior e envia ao Telegram um relatório curto em formato Kanban. O sistema age como uma secretária: diz o que responder, o que realizar e o que ficou pendente.

Princípios: custo mínimo (Claude Haiku 5.5 pela API, centavos por mês), leitura em menos de 1 minuto no celular, e memória entre os dias.

## 2. Fluxo diário

Uma execução por dia, em sete etapas. As etapas 2 a 6 rodam em sequência; a 7 roda o dia todo, em paralelo.

1. **Disparo**: agendador inicia o script no horário definido (ex.: 07:00, America/Sao\_Paulo).
2. **Coleta**: busca e-mails recebidos desde o último relatório (24 h; na segunda, desde o relatório de sábado), ignorando spam, promoções e remetentes da lista de exclusão.
3. **Memória de ontem**: carrega o quadro Kanban salvo, os e-mails enviados ontem e as tarefas marcadas como feitas pelo Telegram.
4. **Análise com IA**: envia ao Claude Haiku 5.5 os e-mails resumidos (remetente, assunto e corpo truncado) + quadro atual; recebe de volta um JSON com tarefas novas, prioridades, rascunhos de resposta e tarefas que viraram "feitas".
5. **Atualização do quadro**: grava as mudanças no banco (novas tarefas, movimentações, pendências que atrasaram).
6. **Envio**: formata a mensagem e envia pelo bot do Telegram; se o áudio estiver ativado, envia também o resumo como mensagem de voz.
7. **Interação**: o bot escuta comandos durante o dia para mover tarefas, alimentando a memória do dia seguinte.

Regra de falha: se a IA ou o e-mail falharem, o bot envia um aviso curto em vez de ficar em silêncio.

## 3. Stack sugerida

Python com bibliotecas oficiais. Só a IA tem custo, estimado em centavos por mês. Itens marcados com (?) dependem das perguntas da seção 10.

| Camada | Escolha | Observação |
| --- | --- | --- |
| Linguagem | Python 3.12 | Bom suporte a todas as APIs abaixo |
| E-mail | Modular: lista de `contas` no config.yaml, cada uma com seu `tipo` | Gmail por IMAP + senha de app (primeira conta: Gmail pessoal); Outlook e corporativo por OAuth (`outlook_oauth`) depois |
| IA | API do Claude, modelo Haiku 5.5 (claude-haiku-5-5) | SDK oficial anthropic; chave criada no Claude Console; saída em JSON |
| IA local&#32; | Ollama + modelo pequeno (ex.: qwen3.5:4b) | Desligado por padrão; reserva se a API falhar ou para e-mails sensíveis. Exige \~4–7 GB de RAM livres no servidor |
| Áudio&#32; | Piper (TTS local) + ffmpeg | Gera o resumo em voz e converte para OGG/Opus, formato das mensagens de voz do Telegram; leve, roda na CPU |
| Mensagens | Bot do Telegram (python-telegram-bot) | Mensagem em HTML para negrito e emojis |
| Memória | SQLite | Arquivo em `data/`, montado como volume do container |
| Agendamento | Agendador interno do python-telegram-bot | Tudo roda num container Docker (docker-compose.yml) no servidor Ubuntu, visível no gerenciador de containers |
| Config | Arquivo .env + config.yaml | Segredos no .env, preferências no YAML |
| Testes | pytest | Com e-mails fictícios de exemplo |

**Custo estimado da IA**

O Haiku 5.5 custa US$ 0,10 por milhão de tokens de entrada e US$ 0,50 por milhão de saída, para chamadas de até 100 mil tokens; acima disso, o preço fica 5× maior ([preços](https://platform.claude.com/docs/en/docs/about-claude/pricing)). Estimativas para 22 dias úteis por mês:

| Cenário | Entrada/dia (tokens) | Saída/dia (tokens) | Custo/mês (US$) |
| --- | --- | --- | --- |
| 40 e-mails, só remetente e assunto | \~5 mil | \~2 mil | \~0,03 |
| 40 e-mails, corpo resumido + rascunhos (padrão) | \~20 mil | \~4 mil | \~0,09 |
| 100 e-mails, corpo completo + rascunhos | \~85 mil | \~8 mil | \~0,28 |

A assinatura do Claude e a API são cobradas à parte. O plano Max inclui crédito mensal de API (US$ 100 no Max 5x, US$ 200 no Max 20x), que não acumula; o Pro não inclui ([detalhes](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans)).

Cuidados no código:

- Manter cada chamada abaixo de 100 mil tokens, dividindo os e-mails em lotes se preciso.
- Definir limite de gasto no Claude Console, como proteção contra bugs em loop.
- Registrar no log os tokens de cada resposta (campo usage) para acompanhar o custo real.

**Camada de IA trocável**

O código nunca chama a API do Claude diretamente: tudo passa por uma interface única (`analisar(emails, quadro) → JSON`), com um provedor para cada IA. Isso não custa nada agora e deixa três opções abertas:

- **Reserva**: se a API do Claude falhar, o sistema tenta o Ollama local antes de enviar o aviso de falha.
- **Roteamento por sensibilidade**: e-mails de domínios listados no config (ex.: o da empresa) vão só para o modelo local.
- **Troca total**: mudar `llm.provider` no config.yaml troca a IA sem mexer no código.

```yaml
llm:
  provider: claude          # claude | ollama
  fallback: none            # none | ollama
  local_only_domains: []    # ex.: ["empresa.com.br"]
audio:
  enabled: false            # true = envia o resumo também como voz
```

O modelo local é mais lento e erra mais em português que o Haiku 5.5; serve como reserva e para privacidade, não como padrão.

## 4. Memória: o que foi feito ontem

O sistema só sabe o que você fez se houver um registro. Proposta de três fontes, da mais automática à mais manual:

- **E-mails enviados**: se você respondeu um e-mail que estava em "Responder", a tarefa vai para "Feito" automaticamente.
- **Comandos no Telegram**: `/feito 3` move a tarefa 3 para "Feito"; `/nova Ligar para o contador` cria uma tarefa.
- **Nota livre no Telegram**: qualquer mensagem de texto ao bot vira registro do dia (ex.: "Fechei o orçamento com o cliente X").

Cada execução salva um retrato do quadro (snapshot) por data. Assim o relatório compara hoje com ontem e mostra o que foi concluído e o que está atrasado.

## 5. Quadro Kanban

Quatro colunas, cada tarefa com um tipo (Responder ou Realizar) e uma prioridade (Alta, Média, Baixa).

| Coluna | O que entra | Regra de saída |
| --- | --- | --- |
| A Fazer | Tarefas novas e pendências | Vai para Em Andamento ou Feito por comando ou e-mail enviado |
| Em Andamento | Tarefas iniciadas | `/feito` move para Feito |
| Aguardando | Depende de resposta de outra pessoa | Volta para A Fazer quando a resposta chega por e-mail |
| Feito | Concluídas no dia anterior | Sai do relatório no dia seguinte (fica no histórico) |

Regras gerais:

- Cada tarefa tem um número curto e fixo, usado nos comandos.
- Tarefa em A Fazer há mais de 3 dias ganha marca de atraso (o número de dias é configurável).
- A IA não duplica tarefas: e-mails do mesmo assunto atualizam a tarefa existente.
- No máximo 5 itens por coluna no relatório; o resto aparece como "+N tarefas".

## 6. Exemplo do relatório no Telegram

Resumo no topo, depois as colunas. Emojis só como marcadores visuais, para leitura rápida no celular.

```
☀️ Bom dia! Quarta, 28/10
3 para responder · 2 para realizar · 1 atrasada

✅ FEITO ONTEM
• Respondeu João sobre o contrato
• Enviou relatório mensal

📋 A FAZER
🔴 #12 Responder: Maria – aprovar orçamento (hoje)
🟡 #14 Realizar: Emitir nota fiscal do cliente X
🟢 #15 Responder: Convite para webinar
⚠️ #9 Ligar para o contador (há 4 dias)

🔄 EM ANDAMENTO
• #7 Proposta para empresa Y

⏳ AGUARDANDO
• #5 Retorno do fornecedor sobre prazo

💡 Foco do dia: aprovar o orçamento da Maria antes das 12h.

Comandos: /feito 12 · /nova texto · /quadro
```

## 7. Estrutura do projeto

Um módulo por responsabilidade, para que cada fase seja implementada e testada isoladamente.

```
assistente-email/
├── CLAUDE.md            # este plano + convenções do projeto
├── .env.example         # chaves: ANTHROPIC, TELEGRAM, credenciais de e-mail
├── config.yaml          # horário, contas, filtros, remetentes VIP, kanban
├── Dockerfile / docker-compose.yml
├── main.py              # orquestra o fluxo diário
├── src/
│   ├── email_reader.py  # coleta e filtra e-mails
│   ├── ai_analyzer.py   # prompt + validação do JSON + log de tokens
│   ├── llm/
│   │   ├── base.py      # interface única: analisar(emails, quadro) → JSON
│   │   ├── claude.py    # provedor padrão (Haiku 5.5)
│   │   └── ollama.py    # provedor local opcional (Fase 8)
│   ├── tts.py           # áudio opcional: Piper + ffmpeg (Fase 8)
│   ├── kanban.py        # regras do quadro (mover, atrasar, deduplicar)
│   ├── storage.py       # SQLite: tarefas, histórico, snapshots
│   ├── report.py        # monta a mensagem do Telegram
│   └── telegram_bot.py  # envio + comandos (/feito, /nova, /quadro)
├── prompts/
│   └── analise.md       # prompt da IA, editável sem mexer no código
└── tests/
    └── fixtures/        # e-mails fictícios para teste
```

## 8. Fases de implementação

Oito fases, cada uma entregue e testada antes da próxima. Peça ao Claude Code uma fase por vez.

| Fase | Entrega | Pronto quando |
| --- | --- | --- |
| 1. Base | Estrutura de pastas, .env, config.yaml, storage.py com tabelas, container | `python main.py --check` valida as chaves e cria o banco |
| 2. E-mail | email\_reader.py com filtros | Lista no terminal os e-mails das últimas 24 h, sem spam |
| 3. IA | ai\_analyzer.py + interface de provedor (só Claude por enquanto) + prompt em arquivo | Com e-mails fictícios, retorna JSON válido com tarefas, prioridades e rascunhos, e registra os tokens usados |
| 4. Kanban | kanban.py com regras da seção 5 | Testes cobrem mover, deduplicar e marcar atraso |
| 5. Telegram (envio) | report.py + envio pelo bot | Relatório igual ao exemplo da seção 6 chega no celular |
| 6. Telegram (comandos) | /feito, /nova, /quadro, /rascunho, nota livre, Google Agenda (iCal), resumo semanal | Comando altera o banco e aparece no relatório seguinte |
| 7. Agendamento | Agendador no container + logs + aviso de falha | Roda sozinho 3 dias seguidos sem intervenção |
| 8. Extras | Provedor Ollama + áudio do resumo (tts.py) | Trocar llm.provider no config.yaml muda a IA sem mexer no código, e o resumo chega também como mensagem de voz |

Modo de teste: uma flag `--dry-run` imprime o relatório no terminal sem enviar nada nem alterar o banco.

## 9. Segurança e privacidade

A IA é a API paga do Claude, que não usa os dados para treinar modelos. Isso permite enviar o corpo resumido dos e-mails, necessário para rascunhos de resposta úteis.

- **Política da API do Claude**: dados não são usados para treino e são apagados em até 30 dias, salvo violação da política de uso ou exigência legal ([fonte](https://privacy.claude.com/en/articles/7996866)).
- **Por que não o Gemini gratuito**: nele o Google pode usar o conteúdo para melhorar produtos, e revisores humanos podem lê-lo ([termos](https://ai.google.dev/gemini-api/terms)).
- **Minimizar o que é enviado**: corpo truncado em \~1.500 caracteres; remover anexos, assinaturas, histórico citado e números longos (CPF, contas).
- **E-mails empresariais**: confirmar se a política da empresa permite enviar dados de clientes a um serviço externo.
- **Modo local (opcional)**: com o provedor Ollama, os e-mails dos domínios em `local_only_domains` não saem do servidor.
- **Acesso ao e-mail somente leitura**: escopo `gmail.readonly` (ou equivalente no IMAP); o sistema nunca envia nem apaga e-mails.
- **Bot restrito ao seu chat**: o bot ignora qualquer mensagem que não venha do seu chat\_id.
- **Segredos fora do código**: chaves no .env, que entra no .gitignore.

## 10. Perguntas em aberto

Responda abaixo de cada pergunta (ou edite a seção correspondente). Marque a caixa quando decidir.

**Infraestrutura**

- [x] Qual provedor de e-mail: Gmail, Outlook ou corporativo? Os tres. Uma conta com possibilidade para adicionar outras
- [x] Onde o sistema vai rodar: Servidor linux ubuntu ligado 24h? (Os comandos do Telegram exigem algo ligado o dia todo.)
- [x] Horário do relatório? 7hr Envia também nos fins de semana? não

**Conteúdo do relatório**

- [x] Quais e-mails ignorar (newsletters, notificações de sistemas) e quais remetentes são VIP (sempre prioridade alta)? Ignora propagandas, spam e promoções, focar em e-mails pessoais, empresariais e como por exemplo de sites de freelancer. Criar um parâmetro para adicionar e-mails específicos para a IA pegar
- [x] As 4 colunas do Kanban (A Fazer, Em Andamento, Aguardando, Feito) servem, ou quer outras? Servem
- [x] A IA deve sugerir um rascunho de resposta para os e-mails de "Responder"? Sim
- [x] Quer um resumo semanal (ex.: sexta à tarde) além do diário? Quero

**Memória e interação**

- [x] Além dos e-mails, outras fontes do "o que fiz": Google Agenda, notas manuais no Telegram, outra ferramenta? Google agenda e notas manuais direto no chat do bot como texto puro ou comando /nova
- [x] Quer interagir com o bot (comandos) ou apenas receber o relatório? Interagir
- [x] Quer ver o quadro também fora do Telegram (Trello, Notion, ClickUp)? Não
- [x] Após quantos dias uma tarefa parada deve ser marcada como atrasada? 3 dias

**Privacidade**

- [x] Aceita enviar o conteúdo dos e-mails à IA, ou prefere só remetente + assunto? Corpo resumido (\~1.500 caracteres) via API do Claude, que não usa os dados para treino (decisão revista ao trocar o Gemini gratuito pelo Claude)

## 11. Como usar no Claude Code

Depois de responder as perguntas, exporte este doc em Markdown e salve como `CLAUDE.md` na raiz de uma pasta vazia. Em seguida, use estes prompts em ordem:

1. "Leia o CLAUDE.md e me diga se falta alguma decisão antes de começar."
2. "Implemente a Fase 1 e pare para eu testar."
3. Repita para cada fase, confirmando o critério "Pronto quando" antes de avançar.

Você vai precisar criar manualmente: a chave da API do Claude (Claude Console; no plano Max, vincule o crédito mensal em Configurações > Cobrança no claude.ai), o bot do Telegram (@BotFather) e a senha de app do Gmail (Conta Google > Segurança). O Claude Code pode guiar cada passo.

## 12. Decisões tomadas antes da Fase 1 (29/10/2026)

- **Contas**: lista `contas` no config.yaml. A primeira é o Gmail pessoal, por IMAP com senha de app. Endereço e senha ficam no `.env` (o repositório é público). Outlook e corporativo entram depois com OAuth (`tipo: outlook_oauth`).
- **Execução**: tudo em container Docker (docker-compose.yml), acompanhado pelo gerenciador de containers do servidor. O banco SQLite fica em `data/`, montado como volume.
- **Dias**: relatório de segunda a sábado, às 07:00. O relatório de segunda cobre tudo desde o relatório de sábado, incluindo o restante do sábado até 23:59. O atraso conta 3 dias úteis, e dias úteis são os de `geral.dias_uteis` (seg a sáb).
- **Filtros**: `excluir_remetentes` (nunca entram), `incluir_sempre` (nunca são filtrados, ex.: sites de freelancer) e `vip` (sempre prioridade Alta), todas começando vazias.
- **Rascunhos**: não aparecem no relatório. O comando `/rascunho N` mostra o rascunho da tarefa N.
- **Resumo semanal**: sábado às 17:00, com as tarefas concluídas na semana, as pendentes, as mais atrasadas e o custo da IA na semana.
- **Google Agenda**: lida pelo endereço secreto iCal (`GOOGLE_AGENDA_ICAL_URL`), sem OAuth.
- **Várias contas**: um relatório único, com a conta marcada em cada item.
- **E-mail da empresa**: confirmar a política antes de adicionar a conta. Se não puder sair do servidor, o domínio vai para `local_only_domains` e só é analisado com o Ollama (Fase 8).
