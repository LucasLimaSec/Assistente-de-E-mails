# Plano: Assistente de E-mails (Claude Code)

Oct 28, 2026

## 1. Objetivo

Todo dia de manhã, um script lê os e-mails das últimas 24 h, cruza com o que foi feito no dia anterior e envia ao Telegram um relatório curto em formato Kanban. O sistema age como uma secretária: diz o que responder, o que realizar e o que ficou pendente.

Princípios: custo mínimo (Claude Haiku 5.5 pela API, centavos por mês), leitura em menos de 1 minuto no celular, e memória entre os dias.

## 2. Fluxo diário

Uma execução por dia, em sete etapas. As etapas 2 a 6 rodam em sequência; a 7 roda o dia todo, em paralelo.

1. **Disparo**: agendador inicia o script no horário definido (08:00, America/Sao\_Paulo).
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
| IA | Três provedores trocáveis: `ollama` (padrão por enquanto), `regras` (reserva, sem IA) e `claude` (API, quando houver crédito) | Ver seção 13 |
| IA local&#32; | Ollama + modelo pequeno (ex.: qwen3.5:4b) em container no servidor | Padrão por enquanto, em lotes de madrugada (seção 13). Depois pode apontar para o Ollama do PC de mesa |
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
  provider: ollama          # regras | ollama | claude
  fallback: regras          # none | regras | ollama
  local_only_domains: []    # ex.: ["empresa.com.br"]
audio:
  enabled: false            # true = envia o resumo também como voz
```

O modelo local é mais lento e erra mais em português que o Haiku 5.5. É o padrão por enquanto só porque não tem custo (seção 13); quando houver crédito, o Claude volta a ser a melhor opção.

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
│   │   ├── regras.py    # provedor sem IA (reserva)
│   │   ├── ollama.py    # provedor local (padrão por enquanto)
│   │   └── claude.py    # provedor API (Haiku 5.5)
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
| 3. IA | ai\_analyzer.py + provedores `regras`, `ollama` e `claude` + container do Ollama + prompt em arquivo + `--benchmark-ia` | Com e-mails fictícios: o Ollama no servidor retorna JSON válido em lotes, no tempo medido pelo benchmark; `regras` sempre funciona; `claude` passa nos testes com a API simulada; tokens registrados |
| 4. Kanban | kanban.py com regras da seção 5 | Testes cobrem mover, deduplicar e marcar atraso |
| 5. Telegram (envio) | report.py + envio pelo bot | Relatório igual ao exemplo da seção 6 chega no celular |
| 6. Telegram (comandos) | /feito, /nova, /quadro, /rascunho, nota livre, Google Agenda (iCal), resumo semanal | Comando altera o banco e aparece no relatório seguinte |
| 7. Agendamento | Agendador no container + logs + aviso de falha | Roda sozinho 3 dias seguidos sem intervenção |
| 8. Extras | IA no PC de mesa (Ollama pela rede local ou Tailscale) + áudio do resumo (tts.py) | Trocar a URL ou o provedor no config.yaml muda a IA sem mexer no código, e o resumo chega também como mensagem de voz |

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
- [x] Horário do relatório? 7hr (alterado para 8hr em 30/10/2026, horário de entrada no trabalho) Envia também nos fins de semana? não

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
- **Dias**: relatório de segunda a sábado, às 08:00 (horário de entrada no trabalho). O relatório de segunda cobre tudo desde o relatório de sábado, incluindo o restante do sábado até 23:59. O atraso conta 3 dias úteis, e dias úteis são os de `geral.dias_uteis` (seg a sáb).
- **Filtros**: `excluir_remetentes` (nunca entram), `incluir_sempre` (nunca são filtrados, ex.: sites de freelancer) e `vip` (sempre prioridade Alta), todas começando vazias.
- **Rascunhos**: não aparecem no relatório. O comando `/rascunho N` mostra o rascunho da tarefa N.
- **Resumo semanal**: sábado às 17:00, com as tarefas concluídas na semana, as pendentes, as mais atrasadas e o custo da IA na semana.
- **Google Agenda**: lida pelo endereço secreto iCal (`GOOGLE_AGENDA_ICAL_URL`), sem OAuth.
- **Várias contas**: um relatório único, com a conta marcada em cada item.
- **E-mail da empresa**: confirmar a política antes de adicionar a conta. Se não puder sair do servidor, o domínio vai para `local_only_domains` e só é analisado com o Ollama (Fase 8).

## 13. IA sem custo: modelo pequeno no servidor (30/10/2026)

Sem crédito na API por enquanto. O servidor (i5 de 4ª geração, 8 GB de RAM, sem GPU) roda um modelo pequeno, com três provedores trocáveis pelo config.yaml:

- **`ollama`** (padrão agora): container `ollama/ollama` no mesmo docker-compose, sem porta exposta para fora da rede interna dos containers. `mem_limit` de ~5 GB, um modelo carregado por vez, sem paralelismo, e `keep_alive` curto para liberar a RAM depois da análise.
- **`regras`** (reserva): sem IA. Cada e-mail relevante vira tarefa "Responder" com o assunto, prioridade Alta para VIP e deduplicação pela conversa. Também cobre o que o modelo não terminar a tempo.
- **`claude`** (futuro): Haiku 5.5 pela API, pronto e testado com a API simulada; liga com `provider: claude`.

Como o modelo pequeno é lento e tem pouca memória, o fluxo muda:

- **Análise de madrugada**: a análise começa às 05:30 (`llm.horario_analise`) e o relatório sai às 08:00. O que não ficar pronto até 07:55 vai pelo modo `regras`, assim como os e-mails que chegarem depois do início da análise (se der tempo, eles entram num lote extra antes do relatório).
- **Swap**: o servidor tem 4 GB de swap (`/swap.img`), uma margem contra falta de memória. O benchmark deve confirmar que a análise não depende dele, porque swap deixa tudo muito mais lento.
- **Lotes pequenos**: ~6 e-mails por chamada (`llm.lote_emails`), com contexto de 8 mil tokens (`num_ctx`). Isso limita a RAM e melhora a precisão do modelo pequeno.
- **Pré-filtro por regras**: spam, promoções, excluídos e conversas já conhecidas não passam pelo modelo.
- **Saída curta e estruturada**: o lote só classifica (tipo, prioridade, título curto, prazo), com JSON forçado pelo schema do Ollama (`format`). Saída inválida: uma nova tentativa, depois `regras`.
- **Rascunhos sob demanda**: gerados só quando você pede `/rascunho N` (~1–2 min no servidor) e guardados no banco. Fica configurável (`llm.rascunhos: sob_demanda | lote`) para quando houver IA mais rápida.
- **Benchmark antes de decidir**: `python main.py --benchmark-ia` roda os e-mails fictícios no servidor e mede tempo, RAM e acertos. O modelo e o tamanho do lote saem dessa medição.
- **Futuro**: `llm.ollama.url` aponta hoje para o container (`http://ollama:11434`). Para usar o PC de mesa, basta trocar pela URL dele (rede local ou Tailscale, nunca exposta à internet). Pode virar uma lista em ordem de preferência: PC de mesa primeiro, servidor como reserva.

## 14. Oportunidades de projetos (30/10/2026)

O 99Freelas manda ~14 projetos por dia; virar uma tarefa por projeto encheria o quadro. Decisão:

- **Fontes** (`oportunidades.fontes`): `@99freelas.com.br` e `@workana.com`. Nunca são filtradas e não entram no Kanban: aparecem na seção "💼 Oportunidades" do relatório, com as `max_no_relatorio` (5) de maior aderência e o resto como "+N".
- **Aderência** (0 a 10): a IA compara cada projeto com o perfil profissional, guardado no `.env` (`PERFIL_OPORTUNIDADES`) para não ficar no repositório público. No modo `regras`, a aderência conta as palavras do perfil presentes no projeto.
- **Propaganda dos sites** (ex.: cursos da Workana) fica como "informativo", não como oportunidade.
- **Projeto que interessar**: `/nova` cria a tarefa no quadro (Fase 6).
- **Alertas de segurança do Google** (`no-reply@accounts.google.com`) estão em `vip`: viram tarefa de prioridade Alta.
- **LinkedIn**: continua descartado como newsletter, até decisão contrária.

Regras que o config impõe sobre a IA: `vip` vira sempre tarefa Alta; e-mail de fonte que a IA chamar de tarefa vira oportunidade; "oportunidade" vinda de uma pessoa (fora das fontes) vira tarefa de responder.

**Aprendizado do teste real com Ollama (30/10/2026)**: um modelo pequeno pode entrar em loop gerando itens no JSON até estourar o contexto (6,5 min perdidos num lote). Cada chamada tem teto de saída (`num_predict` = 200 + 150 por e-mail); resposta cortada vira erro, nova tentativa e depois `regras`.

**Benchmark no servidor (31/10/2026)**: `qwen3.5:4b`, lotes de 6, acertou 10 de 10 no gabarito (categoria, tipo, prioridade e aderência). 56 s por e-mail (~28 min para 30 e-mails, folga grande na janela de 05:30 a 07:55), 1,6 tokens/s na saída e 3,1 GB de RAM. Rascunho sob demanda em ~76 s. Decisão: fica o `qwen3.5:4b` com `lote_emails: 6`. Ajuste feito: o prompt de rascunho pedia de volta os dados mascarados como `[número]`; agora ele sabe que são dados ocultados.

**Avisos do 99Freelas (31/10/2026)**: só o trecho do projeto (título, categoria, nível, orçamento, descrição e habilidades) vai para a IA; saudação com o nome, links e rodapé são cortados (`recortar_aviso` em email_reader.py). Se o formato mudar e o marcador não for achado, o e-mail vai inteiro, como antes. A Workana ganha o mesmo recorte quando chegar um aviso real para ver o formato (`--emails --corpo`).

## 15. Quadro Kanban (Fase 4, 01/11/2026)

- **Deduplicação**, da pista mais forte para a mais fraca: mesma conversa (References/In-Reply-To); tarefa aberta apontada pela IA (`tarefa_existente`); mesmo remetente com o mesmo assunto sem `Re:`/`Fwd:`. Mesmo assunto vindo de outra pessoa é outra tarefa. O título da tarefa não muda; a prioridade só sobe e o prazo novo substitui o antigo.
- **Conversa com tarefa aberta não passa pela IA**: o e-mail é só anexado (economiza o modelo lento). Conversa de tarefa em Feito passa pela IA: se for um novo pedido, a tarefa é reaberta com o mesmo número; um "obrigado" (informativo) não reabre.
- **Aguardando**: só entra por comando; volta para A Fazer quando chega e-mail na conversa.
- **E-mail enviado**: lido da pasta marcada como `\Sent` (somente leitura). Uma resposta na conversa de uma tarefa "Responder" em A Fazer ou Em Andamento, enviada depois do último e-mail recebido dela, move a tarefa para Feito. Tarefas "Realizar" só saem por comando.
- **Atraso**: só em A Fazer, contando dias úteis (`geral.dias_uteis`) desde a entrada na coluna; atrasada com **mais de** `dias_para_atraso` (criada na segunda, atrasada na sexta). Mover a tarefa recomeça a contagem. Prazo passado aparece como vencido.
- **Feito no relatório**: só o que foi concluído desde o início da janela (relatório do último dia útil).
- **Banco v2**: a tabela `emails` guarda também categoria, título e aderência, para o relatório listar as oportunidades. Bancos v1 migram sozinhos.
- **Idempotente**: e-mail já registrado não é analisado de novo, então dá para rodar `--atualizar-quadro` várias vezes. Cada rodada fica em `execucoes` com os tokens gastos.

## 16. Relatório no Telegram (Fase 5, 01/11/2026)

- **Comando**: `--relatorio` roda o fluxo completo (coleta, análise, quadro, envio) e guarda o retrato do dia; `--relatorio --dry-run` faz o mesmo numa cópia do banco em memória e só imprime.
- **Formato** (seção 6): cabeçalho com o dia e as contagens (responder/realizar entre A Fazer e Em Andamento, atrasadas); "Feito ontem" (na segunda, "Feito desde sábado"); A Fazer com 🔴🟡🟢 ou ⚠️ "(há N dias)"; Em Andamento e Aguardando só quando têm itens; Oportunidades da janela por aderência; foco do dia; avisos.
- **Prazos**: "(hoje)", "(amanhã)", "(até qua 04/11)", "(venceu 02/11)".
- **Foco do dia** sem IA: a tarefa aberta com prazo vencido ou de hoje; senão a atrasada; senão a de maior prioridade.
- **Conta no item** só aparece com mais de uma conta configurada.
- **Avisos**: falha ao ler uma conta e e-mails classificados pelas regras (sem IA) aparecem no fim do relatório.
- **Limite do Telegram** (4.096 caracteres): o texto é dividido entre seções. Títulos passam por escape de HTML.
- A linha de comandos do exemplo (`/feito 12 · /nova texto · /quadro`) entra na Fase 6, junto com os comandos.
- O log do httpx fica em WARNING: no nível INFO ele registrava a URL com o token do bot.

## 17. Comandos, agenda e resumo semanal (Fase 6, 01/11/2026)

- **Bot**: `python main.py --bot` (polling). Responde só ao `TELEGRAM_CHAT_ID` (filtro em todos os handlers); outros chats não casam com nenhum handler. `concurrent_updates` ligado para um `/rascunho` lento não travar os outros comandos; cada comando abre a própria conexão SQLite numa thread.
- **Comandos**: `/quadro`, `/feito N` (vários números), `/andamento N`, `/aguardando N`, `/afazer N`, `/nova texto` (`/nova ! texto` = alta; tipo Realizar, origem telegram), `/rascunho N` (`novo` gera outro), `/semana`, `/ajuda`. Texto livre vira nota; notas desde o último relatório aparecem em "Feito" com 📝. A lógica fica em `comandos.py` (funções puras), e o `telegram_bot.py` só liga os handlers.
- **/rascunho**: o corpo do e-mail não é guardado, então o e-mail mais recente da tarefa é relido pelo IMAP (`SEARCH HEADER Message-ID`, somente leitura), passa pela mesma limpeza e vai para o `llm.provider`. O rascunho fica em `tarefas.rascunho` e é reaproveitado. Tarefa sem e-mail (criada pelo Telegram) não tem rascunho.
- **Google Agenda**: iCal baixado a cada relatório (`icalendar` + `recurring-ical-events` para eventos repetidos; cancelados ignorados). Eventos desde o último relatório até o fim de ontem entram em "Feito" com 📅; os de hoje em "📅 Agenda de hoje" (dia inteiro primeiro). A URL nunca aparece em mensagens de erro.
- **Resumo semanal**: `--resumo-semanal` (com `--dry-run`) e `/semana`. Semana = segunda 00:00 até agora: concluídas (até 10), pendentes, 3 mais atrasadas, notas, oportunidades (a melhor), execuções e tokens; custo estimado só com o provedor `claude` (US$ 0,10/0,50 por milhão). O envio automático no sábado às 17:00 vem na Fase 7.
- **Relatório**: termina com a linha de comandos (`/feito N` com o número da primeira tarefa de A Fazer).

## 18. Agendamento, logs e avisos (Fase 7, 01/11/2026)

- **Serviço**: `python main.py --servico` é o CMD do container (`restart: unless-stopped`): bot + JobQueue do python-telegram-bot (extra `job-queue`, APScheduler). No JobQueue, 0 = domingo (`dias_ptb` converte).
- **Horários**: análise em `llm.horario_analise` nos dias úteis, com prazo 5 min antes do relatório e até 5 rodadas enquanto chegarem e-mails novos; relatório em `geral.horario_relatorio` com `prazo_limite = agora` (o que sobrou vai pelas regras e aparece o aviso "classificado sem IA"); resumo semanal em `resumo_semanal.dia/horario`.
- **Trava**: análise e relatório nunca rodam juntos. O relatório espera a análise por até 30 min; depois disso manda um aviso em vez do relatório. A análise não roda se outra rotina estiver em andamento.
- **Recuperação ao subir**: o que deveria ter rodado hoje e não rodou roda na hora (relatório se passou do horário e não há retrato do dia; análise se está dentro da janela dela; resumo se é o dia, passou do horário e não há execução 'semanal'). O retrato do dia (`snapshots`) marca "relatório enviado", por isso só o `--relatorio` grava retrato.
- **Avisos**: falha na análise vira aviso no relatório seguinte (nada de mensagem às 05:30); falha no relatório ou no resumo vira mensagem curta no Telegram; falha do próprio Telegram só vai para o log.
- **Logs**: terminal + `logs/assistente.log` com rotação (1 MB × 6); apscheduler e httpx em WARNING. Sem permissão na pasta, segue só no terminal. Docker com `json-file` limitado a 3 × 10 MB.
- **Saúde**: batimento em `data/batimento` a cada 5 min; healthcheck do compose falha se tiver mais de 15 min. `/status` no bot mostra as últimas execuções e os próximos horários.
- **Atenção**: com o serviço no ar, não rode `--bot` em paralelo (dois `getUpdates` com o mesmo token dão conflito). Os outros comandos avulsos podem rodar com `docker compose run`.

