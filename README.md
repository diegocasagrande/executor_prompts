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
| **Prompts** | Entrada. A = ID, B = Prompt, C = Status, D = Fornecedor (opcional), E = Modelo (opcional, ID exato) |
| **Execuções** | Log de auditoria. Cada chamada acrescenta uma linha (23 colunas) |
| **Sumário** | Recalculado ao fim de cada execução: sucesso/erro por fornecedor; execuções, tokens e latência média por modelo |
| **Modelos** | Catálogo de modelos por fornecedor, preenchido por `GET /modelos/{fornecedor}` |

### Regras de execução

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
  "max_tokens": 4096,
  "system_prompt": null
}
```

Uma lista vazia aceita o modelo indicado em cada linha da aba Prompts. Os nomes de fornecedor aceitam apelidos: `google`, `anthropic`, `gpt`. A resposta informa a planilha gerada, cada execução, o que foi ignorado e por quê, e o sumário por fornecedor.

### `GET /modelos/{fornecedor}`

Consulta a API do fornecedor e substitui a lista dele na aba Modelos da última versão, sem criar versão nova.

### `GET /fornecedores`

Mostra os fornecedores e se há credencial configurada.

## Colunas da aba Execuções

ID da execução · ID do prompt · Fornecedor · Modelo solicitado · Modelo efetivo · ID da resposta · Data · Hora · Latência (ms) · Tokens de entrada · Tokens de saída · Tokens de cache · Total de tokens · Motivo de parada · Temperatura · Máximo de tokens de saída · Status · Mensagem de erro · Nº de tentativas · Texto da resposta · Dono da API · Final da chave · System prompt

Observações para comparar fornecedores:

- **Tokens:** no Claude, `input_tokens` não inclui o cache. Por isso o total do Claude soma entrada + saída + cache lido + cache gravado. Cada fornecedor usa seu próprio tokenizador, então as contagens não são comparáveis entre fornecedores.
- **Temperatura:** a Messages API atual da Anthropic não aceita `temperature`, e o Claude registra `não suportada`. Na OpenAI e no Gemini, se o modelo recusar o parâmetro, a chamada é refeita sem ele e a coluna também registra `não suportada`.
- **Dono da API:** vem das variáveis `*_DONO` do `.env`. Quando o fornecedor informa nos headers, são acrescentados `org=` (Anthropic e OpenAI) e `projeto=` (OpenAI).

## Testes

```bash
pytest
```

Os testes não chamam as APIs reais: usam provedores falsos e respostas HTTP simuladas.
