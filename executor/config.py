"""Configuração lida do arquivo .env (nunca versionado)."""

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

PASTA_PLANILHAS = Path(os.getenv("PASTA_PLANILHAS") or RAIZ / "planilha")
if not PASTA_PLANILHAS.is_absolute():
    PASTA_PLANILHAS = RAIZ / PASTA_PLANILHAS

PREFIXO_PLANILHA = os.getenv("PREFIXO_PLANILHA", "PromptExecucoesSumario")

MAX_TENTATIVAS = int(os.getenv("MAX_TENTATIVAS", "5"))

# Claude pela assinatura (Claude Code logado), sem chave de API
CLAUDE_CODE_BIN = os.getenv("CLAUDE_CODE_BIN", "claude")
CLAUDE_CODE_TIMEOUT = int(os.getenv("CLAUDE_CODE_TIMEOUT", "300"))
# System prompt neutro enviado ao Claude Code quando a linha não tem system prompt.
# Ele SUBSTITUI o system prompt padrão do Claude Code (assistente de programação).
CLAUDE_CODE_SYSTEM_PROMPT_PADRAO = os.getenv(
    "CLAUDE_CODE_SYSTEM_PROMPT_PADRAO", "Responda à solicitação do usuário."
).strip()

# Fornecedor -> (variável da chave, variável do dono)
VARIAVEIS = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_DONO"),
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_DONO"),
    "openai": ("OPENAI_API_KEY", "OPENAI_DONO"),
    "claude_code": ("", "CLAUDE_CODE_DONO"),  # sem chave: usa o login do Claude Code
}


def chave(fornecedor: str) -> str:
    variavel = VARIAVEIS[fornecedor][0]
    return os.getenv(variavel, "").strip() if variavel else ""


def dono(fornecedor: str) -> str:
    return os.getenv(VARIAVEIS[fornecedor][1], "").strip()


def final_da_chave(fornecedor: str) -> str:
    k = chave(fornecedor)
    return f"...{k[-4:]}" if len(k) >= 8 else ""


def tem_credencial(fornecedor: str) -> bool:
    if fornecedor == "claude_code":
        return shutil.which(CLAUDE_CODE_BIN) is not None
    return bool(chave(fornecedor))
