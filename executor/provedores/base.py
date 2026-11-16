"""Formato comum de retorno e lógica de retentativas para os três fornecedores."""

import time
from dataclasses import dataclass, field

NOMES = {"gemini": "Gemini", "claude": "Claude", "openai": "OpenAI", "claude_code": "Claude (assinatura)"}

APELIDOS = {
    "gemini": "gemini", "google": "gemini",
    "claude": "claude", "anthropic": "claude",
    "openai": "openai", "gpt": "openai", "chatgpt": "openai",
    "claude_code": "claude_code", "claude-code": "claude_code", "claude (assinatura)": "claude_code",
    "assinatura": "claude_code",
}


def normaliza_fornecedor(nome) -> str | None:
    if not nome:
        return None
    return APELIDOS.get(str(nome).strip().lower())


@dataclass
class Parametros:
    temperatura: float | None = 0
    max_tokens: int = 4096
    system_prompt: str | None = None


@dataclass
class Resultado:
    """O que cada provedor devolve, já no formato das colunas da aba Execuções."""

    sucesso: bool = False
    texto: str = ""
    modelo_efetivo: str = ""
    id_resposta: str = ""
    tokens_entrada: int | None = None
    tokens_saida: int | None = None
    tokens_cache: int | None = None
    total_tokens: int | None = None
    motivo_parada: str = ""
    latencia_ms: int | None = None
    tentativas: int = 0
    erro: str = ""
    dono_api: str = ""
    temperatura_usada: object = None  # número, ou "não suportada"
    extras: dict = field(default_factory=dict)


class ErroTransitorio(Exception):
    """Erro que vale a pena tentar de novo (429, 5xx, sobrecarga, conexão)."""


def com_retentativas(chamada, eh_transitorio, max_tentativas: int):
    """Executa `chamada()` repetindo em erros transitórios com backoff exponencial.

    Devolve (resultado, tentativas, latencia_ms_da_ultima_tentativa).
    Em falha definitiva, relança a exceção com o atributo `tentativas`.
    """
    for tentativa in range(1, max_tentativas + 1):
        inicio = time.perf_counter()
        try:
            resposta = chamada()
            latencia = int((time.perf_counter() - inicio) * 1000)
            return resposta, tentativa, latencia
        except Exception as erro:  # noqa: BLE001 - classificado abaixo
            if tentativa == max_tentativas or not eh_transitorio(erro):
                erro.tentativas = tentativa
                raise
            time.sleep(0.5 * 2 ** (tentativa - 1))
    raise RuntimeError("inalcançável")


def rejeita_temperatura(erro: Exception) -> bool:
    """Alguns modelos (ex.: modelos de raciocínio) recusam o parâmetro temperature."""
    texto = str(erro).lower()
    return "temperature" in texto and any(
        p in texto for p in ("unsupported", "not supported", "does not support", "deprecated", "invalid")
    )
