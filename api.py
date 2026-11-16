"""API REST do executor de prompts.

Rodar:  uvicorn api:app --reload
Docs:   http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from executor import config, servico
from executor.provedores.base import NOMES, Parametros

app = FastAPI(
    title="Executor de prompts",
    description="Executa prompts da planilha nos modelos Gemini, Claude e OpenAI e registra para auditoria.",
    version="2.0.0",
)


class RequisicaoExecucao(BaseModel):
    modelos: dict[str, list[str]] = Field(
        ...,
        description=(
            "Fornecedor -> lista de IDs exatos de modelo. Lista vazia = aceita o modelo "
            "indicado em cada linha da aba Prompts. Fornecedores sem chave no .env são ignorados."
        ),
        examples=[{"claude": ["claude-haiku-5-5"], "gemini": ["gemini-3.5-flash-lite"]}],
    )
    repeticoes: int = Field(1, ge=1, le=100, description="Quantas vezes rodar cada prompt em cada modelo")
    temperatura: float | None = Field(0, ge=0, le=2, description="null = não enviar (padrão do modelo)")
    max_tokens: int = Field(4096, ge=1, le=128000)
    system_prompt: str | None = None


@app.get("/fornecedores")
def fornecedores():
    """Fornecedores conhecidos e se há credencial configurada no .env."""
    return [{"fornecedor": k, "nome": v, "credencial": config.tem_credencial(k)} for k, v in NOMES.items()]


@app.post("/execucoes")
def executar(req: RequisicaoExecucao):
    """Executa os prompts com Status 'Pronto para executar' e gera nova versão da planilha."""
    try:
        return servico.executar_lote(
            req.modelos,
            repeticoes=req.repeticoes,
            parametros=Parametros(req.temperatura, req.max_tokens, req.system_prompt),
        )
    except servico.ErroDeEntrada as erro:
        raise HTTPException(422, str(erro)) from erro
    except FileNotFoundError as erro:
        raise HTTPException(404, str(erro)) from erro


@app.get("/modelos/{fornecedor}")
def modelos(fornecedor: str):
    """Consulta os modelos disponíveis no fornecedor e grava na aba Modelos da última versão."""
    try:
        return servico.atualizar_modelos(fornecedor)
    except servico.ErroDeEntrada as erro:
        raise HTTPException(422, str(erro)) from erro
    except FileNotFoundError as erro:
        raise HTTPException(404, str(erro)) from erro
