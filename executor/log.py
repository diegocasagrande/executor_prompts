"""Log das execuções: terminal (onde roda o uvicorn) e arquivo em logs/executor.log.

Configuração no .env:
- LOG_NIVEL: DEBUG, INFO (padrão) ou WARNING
- LOG_RESPOSTA_TERMINAL: quantos caracteres da resposta mostrar no terminal (padrão 500; 0 = não mostra)
- LOG_RESPOSTA_ARQUIVO: quantos caracteres gravar no arquivo (padrão 0 = resposta completa)
- PASTA_LOGS: pasta do arquivo de log (padrão logs)
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from executor import config

NIVEL = os.getenv("LOG_NIVEL", "INFO").upper()
RESPOSTA_TERMINAL = int(os.getenv("LOG_RESPOSTA_TERMINAL", "500"))
RESPOSTA_ARQUIVO = int(os.getenv("LOG_RESPOSTA_ARQUIVO", "0"))
PASTA_LOGS = Path(os.getenv("PASTA_LOGS") or config.RAIZ / "logs")
if not PASTA_LOGS.is_absolute():
    PASTA_LOGS = config.RAIZ / PASTA_LOGS

FORMATO = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

# O texto da resposta vai num logger separado, para ter tamanhos diferentes no terminal e no arquivo.
log = logging.getLogger("executor")
log_resposta_terminal = logging.getLogger("executor.resposta.terminal")
log_resposta_arquivo = logging.getLogger("executor.resposta.arquivo")

_configurado = False


def configurar() -> None:
    global _configurado
    if _configurado:
        return
    _configurado = True

    terminal = logging.StreamHandler(sys.stdout)
    terminal.setFormatter(FORMATO)

    PASTA_LOGS.mkdir(parents=True, exist_ok=True)
    arquivo = RotatingFileHandler(PASTA_LOGS / "executor.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    arquivo.setFormatter(FORMATO)

    log.setLevel(NIVEL)
    log.propagate = False
    log.addHandler(terminal)
    log.addHandler(arquivo)

    for logger, handler in ((log_resposta_terminal, terminal), (log_resposta_arquivo, arquivo)):
        logger.setLevel(NIVEL)
        logger.propagate = False
        logger.addHandler(handler)


def _corta(texto: str, limite: int) -> str:
    texto = texto or ""
    if limite and len(texto) > limite:
        return f"{texto[:limite]}… [+{len(texto) - limite} caracteres]"
    return texto


def _recuo(texto: str) -> str:
    return "\n".join("    " + linha for linha in texto.splitlines()) or "    (vazia)"


def resposta(texto: str) -> None:
    """Mostra a resposta resumida no terminal e completa (ou no limite configurado) no arquivo."""
    if RESPOSTA_TERMINAL:
        log_resposta_terminal.info("Resposta:\n%s", _recuo(_corta(texto, RESPOSTA_TERMINAL)))
    log_resposta_arquivo.info("Resposta:\n%s", _recuo(_corta(texto, RESPOSTA_ARQUIVO)))
