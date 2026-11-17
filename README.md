# Executor de prompts

Executa os prompts da planilha nos modelos **Gemini**, **Claude** e **OpenAI** e registra cada chamada para auditoria. A planilha é local e versionada: cada execução gera uma nova versão e a anterior nunca é alterada.

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env-exemplo .env    # preencha só as chaves que tiver
uvicorn api:app --reload
```

A documentação interativa fica em http://127.0.0.1:8000/docs.

## Planilha

Os arquivos ficam em `planilha/` com o nome `PromptExecucoesSumario_vNNNNN.xlsx`. O programa sempre usa a versão de maior número.

| Aba | Uso |
|---|---|
| **Prompts** | Entrada. A = ID, B = Prompt, C = Status, D = Fornecedor (opcional), E = Modelo (opcional, ID exato), F = System prompt (opcional) |
| **Execuções** | Log de auditoria. Cada chamada acrescenta uma linha (25 colunas; A = Identificador Único, B = Texto do Resultado) |
| **Sumário** | Recalculado ao fim de cada execução: sucesso/erro por fornecedor; execuções, tokens e latência média por modelo |
| **Modelos** | Catálogo de modelos por fornecedor, preenchido por `GET /modelos/{fornecedor}` |

### Regras de execução

- **System prompt:** vem só da coluna F da aba Prompts e vale para aquela linha; vazio = nenhum. A requisição não tem mais esse campo. O valor usado fica registrado na coluna *System prompt* da aba Execuções. No fornecedor `claude_code` ele é acrescentado ao system prompt padrão do Claude Code, não o substitui.
- Só rodam as linhas com Status = `Pronto para executar`.
- As colunas Fornecedor/Modelo da linha definem onde o prompt roda. A requisição filtra:
  - linha com Fornecedor + Modelo: roda se o fornecedor estiver na requisição e, quando a requisição listar modelos para ele, se o modelo estiver na lista;
  - linha só com Fornecedor: roda nos modelos da requisição para esse fornecedor;
  - linha sem Fornecedor/Modelo: roda em todos os modelos da requisição.
- Fornecedores sem chave no `.env` são ignorados.
- Se a aba Modelos tiver o catálogo do fornecedor, o modelo precisa constar nele. Caso contrário, a linha vira um registro de erro, sem chamar a API.
- Se ao menos uma chamada do prompt der certo, o Status muda para `Executado` na nova versão. Se todas falharem, ele continua `Pronto para executar`.
- Quando não há nada a executar, nenhuma versão nova é criada.
- Erros transitórios (429, 5xx, sobrecarga) são repetidos até `MAX_TENTATIVAS` vezes, com backoff exponencial.

## Endpoints

### `POST /execucoes`

```json
{
  "modelos": {
    "claude": ["claude-haiku-5-5"],
    "gemini": ["gemini-3.5-flash-lite"],
    "openai": []
  },
  "repeticoes": 1,
  "temperatura": 0,
  "max_tokens": 4096
}
```

Uma lista vazia aceita o modelo indicado em cada linha da aba Prompts. Os nomes de fornecedor aceitam apelidos: `google`, `anthropic`, `gpt`. A resposta informa a planilha gerada, cada execução, o que foi ignorado e por quê, e o sumário por fornecedor.

### `GET /modelos/{fornecedor}`

Consulta a API do fornecedor e substitui a lista dele na aba Modelos da última versão, sem criar versão nova.

### `GET /fornecedores`

Mostra os fornecedores e se há credencial configurada.

## Claude pela assinatura (sem chave de API)

O fornecedor `claude_code` (apelidos: `assinatura`, `claude-code`) executa `claude -p` com o Claude Code logado na sua conta do plano. As execuções consomem o limite de uso da assinatura em vez de cobrar a API.

```json
{ "modelos": { "assinatura": ["haiku"] } }
```

- Requisitos: Claude Code instalado e logado na máquina (`claude` no PATH, ou `CLAUDE_CODE_BIN` no `.env`).
- Uma linha da aba Prompts com Fornecedor `Claude` roda por aqui quando a requisição pede só `assinatura`. Se a requisição também pedir `claude`, a linha vai pela API.
- Os modelos são os aliases do CLI (`haiku`, `sonnet`, `opus`) ou um ID completo; a coluna **Modelo utilizado** registra o que respondeu. O CLI não lista modelos: `GET /modelos/assinatura` grava os três aliases.
- `ANTHROPIC_API_KEY` é removida do ambiente da chamada, para o CLI não cobrar a API sem você perceber.
- Isolamento: o system prompt padrão do Claude Code (assistente de programação, com diretório, git e CLAUDE.md) é **substituído** pelo da coluna F ou, se vazia, por `CLAUDE_CODE_SYSTEM_PROMPT_PADRAO`. A chamada roda num diretório temporário vazio, sem ferramentas, sem MCP e sem arquivos de configuração. O system prompt efetivamente enviado fica na coluna *System prompt*.
- Limitações: não há controle de temperatura nem de máximo de tokens; o modelo pode usar raciocínio estendido; o limite de uso é compartilhado com o chat. Registrado na planilha como `Claude (assinatura)`, separado do `Claude` por API.
- O alias `haiku` pode apontar para uma versão diferente da que você espera (na v00004 respondeu `claude-haiku-4-5-20251001`). Para fixar a versão, use o ID completo do modelo.
- Não verifiquei se as condições do plano permitem execuções automatizadas em lote por este caminho; confira antes de usar em larga escala.

## Colunas da aba Execuções

**Identificador Único** (ID do prompt) · **Texto do Resultado** · **Modelo utilizado** (o que respondeu) · Data da Execução · Hora da Execução (as cinco da planilha original, nesta ordem) · ID da execução · Fornecedor · Modelo solicitado · ID da resposta · Latência (ms) · Tokens de entrada · Tokens de saída · Tokens de cache · Total de tokens · Motivo de parada · Temperatura · Máximo de tokens de saída · Status · Mensagem de erro · Nº de tentativas · Dono da API · Final da chave · System prompt · Tokens de raciocínio · Nº de turnos

Observações para comparar fornecedores:

- **Tokens de entrada:** entrada total, incluindo o que veio do cache, nos três fornecedores. No Claude a API informa `input_tokens` sem o cache, então o executor soma `input_tokens` + cache lido + cache gravado. *Tokens de cache* mostra só a parte lida do cache.
- **Tokens de saída × texto:** os tokens de saída incluem o raciocínio interno quando o modelo o usa (Claude e OpenAI), por isso podem ser muito maiores que o texto gravado. A coluna *Tokens de raciocínio* separa essa parte (no Gemini ela não está incluída nos tokens de saída, mas sim no total). *Nº de turnos* mostra quantas chamadas ao modelo a execução fez (deve ser 1). Cada fornecedor usa seu próprio tokenizador, então as contagens não são comparáveis entre fornecedores.
- **Temperatura:** a Messages API atual da Anthropic não aceita `temperature`, e o Claude registra `não suportada`. Na OpenAI e no Gemini, se o modelo recusar o parâmetro, a chamada é refeita sem ele e a coluna também registra `não suportada`.
- **Dono da API:** vem das variáveis `*_DONO` do `.env`. Quando o fornecedor informa nos headers, são acrescentados `org=` (Anthropic e OpenAI) e `projeto=` (OpenAI).

## Log

Cada execução aparece no terminal onde roda o `uvicorn` e em `logs/executor.log` (com rotação a cada 10 MB, 5 arquivos):

```
2026-11-17 08:01:12 | INFO    | === Início do lote | seleção={'claude_code': ['haiku']} | repetições=1 | temperatura=0 | max_tokens=4096
2026-11-17 08:01:12 | INFO    | Planilha de origem: PromptExecucoesSumario_v00004.xlsx | prompts prontos: 20 | a executar: 20 prompts, 20 chamadas
2026-11-17 08:01:12 | INFO    | Nova versão: PromptExecucoesSumario_v00005.xlsx
2026-11-17 08:01:12 | INFO    | [1/20] TAREFA01_T | Claude (assinatura) | haiku | repetição 1/1 | início
2026-11-17 08:01:33 | INFO    | [1/20] TAREFA01_T | Claude (assinatura) | haiku | SUCESSO | modelo utilizado=… | latência=21573 ms | tokens entrada=… saída=… raciocínio=… …
2026-11-17 08:01:33 | INFO    | Resposta:
    …
2026-11-17 08:09:40 | INFO    | === Fim do lote | PromptExecucoesSumario_v00005.xlsx | 20 chamadas | 20 sucesso | 0 erro | 508.2 s
```

No terminal a resposta é cortada em `LOG_RESPOSTA_TERMINAL` caracteres; no arquivo vai completa (ou até `LOG_RESPOSTA_ARQUIVO`). Erros transitórios e itens ignorados aparecem como `WARNING`, falhas como `ERROR`. A planilha continua sendo o registro oficial de auditoria; o log serve para acompanhar a execução.

## Isolamento entre execuções

Cada chamada é independente: o executor cria um cliente novo e envia só o prompt (e o system prompt) da própria linha, sem histórico, sessão ou ID de resposta anterior. No `claude_code`, cada chamada roda numa pasta temporária vazia e nova, com `--no-session-persistence` e sem `--continue`/`--resume`. Os testes `test_isolamento_*` conferem as requisições montadas pelos SDKs e pelo CLI.

Os *Tokens de cache* não indicam mistura de contexto: o cache do fornecedor só reaproveita um trecho inicial **idêntico** (em geral o system prompt) para cobrar menos, e não acrescenta nada de uma execução em outra.

Para conferir com as suas credenciais, sem gravar na planilha:

```bash
python scripts/teste_isolamento.py claude_code:haiku
python scripts/teste_isolamento.py claude:claude-haiku-5-5 gemini:gemini-3.5-flash-lite openai:gpt-5-mini
```

O script pede para o modelo memorizar uma palavra-código aleatória numa chamada e pergunta por ela na seguinte (deve responder "NÃO SEI"), e depois pede que descreva todo o contexto que recebeu, apontando termos do ambiente como repositório, diretório ou CLAUDE.md.

## Testes

```bash
pytest
```

Os testes não chamam as APIs reais: usam provedores falsos e respostas HTTP simuladas.
