"""Configuração lida do arquivo .env (nunca versionado)."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

PASTA_PLANILHAS = Path(os.getenv("PASTA_PLANILHAS") or RAIZ / "planilha")
if not PASTA_PLANILHAS.is_absolute():
    PASTA_PLANILHAS = RAIZ / PASTA_PLANILHAS

PREFIXO_PLANILHA = os.getenv("PREFIXO_PLANILHA", "PromptExecucoesSumario")

MAX_TENTATIVAS = int(os.getenv("MAX_TENTATIVAS", "5"))

# Fornecedor -> (variável da chave, variável do dono)
VARIAVEIS = {
    "gemini": ("GEMINI_API_KEY", "GEMINI_DONO"),
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_DONO"),
    "openai": ("OPENAI_API_KEY", "OPENAI_DONO"),
}


def chave(fornecedor: str) -> str:
    return os.getenv(VARIAVEIS[fornecedor][0], "").strip()


def dono(fornecedor: str) -> str:
    return os.getenv(VARIAVEIS[fornecedor][1], "").strip()


def final_da_chave(fornecedor: str) -> str:
    k = chave(fornecedor)
    return f"...{k[-4:]}" if len(k) >= 8 else ""


def tem_credencial(fornecedor: str) -> bool:
    return bool(chave(fornecedor))
