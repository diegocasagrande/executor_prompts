"""Claude pela assinatura: executa `claude -p` (Claude Code) logado na conta do plano.

Não usa chave de API. As execuções consomem o limite de uso da assinatura.
Diferenças em relação ao fornecedor `claude` (API): o Claude Code acrescenta o próprio
system prompt, não permite fixar temperatura nem limitar tokens de saída, e as ferramentas
são desabilitadas para a chamada ser apenas texto.
"""

import json
import os
import subprocess
import time

from executor import config
from executor.provedores.base import Parametros, Resultado

FORNECEDOR = "claude_code"
NAO_SUPORTADO = "não suportada"
FERRAMENTAS_DESABILITADAS = "Bash,Edit,Write,Read,Glob,Grep,WebSearch,WebFetch,Task,NotebookEdit"
TRANSITORIOS = {408, 429, 500, 502, 503, 504, 529}
# Aliases aceitos pelo parâmetro --model do CLI (o CLI não lista modelos).
ALIASES = [("haiku", "alias do Claude Code"), ("sonnet", "alias do Claude Code"), ("opus", "alias do Claude Code")]


class _Transitorio(Exception):
    pass


def _ambiente() -> dict:
    """Remove chaves de API do ambiente: com elas o CLI cobraria a API em vez de usar a assinatura."""
    env = dict(os.environ)
    for variavel in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(variavel, None)
    return env


def _comando(modelo: str, p: Parametros) -> list[str]:
    cmd = [
        config.CLAUDE_CODE_BIN, "-p",
        "--output-format", "json",
        "--no-session-persistence",
        "--disallowedTools", FERRAMENTAS_DESABILITADAS,
        "--model", modelo,
    ]
    if p.system_prompt:
        cmd += ["--append-system-prompt", p.system_prompt]
    return cmd


def _uma_chamada(modelo: str, prompt: str, p: Parametros) -> dict:
    try:
        proc = subprocess.run(
            _comando(modelo, p), input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=config.CLAUDE_CODE_TIMEOUT, env=_ambiente(), cwd=config.RAIZ,
        )
    except subprocess.TimeoutExpired as erro:
        raise _Transitorio(f"tempo esgotado ({config.CLAUDE_CODE_TIMEOUT}s)") from erro

    saida = (proc.stdout or "").strip()
    try:
        dados = json.loads(saida)
    except json.JSONDecodeError as erro:
        detalhe = (proc.stderr or saida or "sem saída").strip()[:500]
        raise RuntimeError(f"saída do claude -p não é JSON (código {proc.returncode}): {detalhe}") from erro

    if dados.get("is_error"):
        status = dados.get("api_error_status")
        mensagem = f"{dados.get('terminal_reason') or 'erro'}: {dados.get('result') or ''}".strip()
        if status in TRANSITORIOS:
            raise _Transitorio(mensagem)
        raise RuntimeError(mensagem)
    return dados


def executar(modelo: str, prompt: str, p: Parametros) -> Resultado:
    tentativas = 0
    dados = None
    erro_final = ""
    for tentativas in range(1, config.MAX_TENTATIVAS + 1):
        inicio = time.perf_counter()
        try:
            dados = _uma_chamada(modelo, prompt, p)
            latencia = int((time.perf_counter() - inicio) * 1000)
            break
        except _Transitorio as erro:
            erro_final = f"{type(erro).__name__}: {erro}"
            if tentativas < config.MAX_TENTATIVAS:
                time.sleep(0.5 * 2 ** (tentativas - 1))
        except Exception as erro:  # noqa: BLE001
            erro_final = f"{type(erro).__name__}: {erro}"
            break

    dono = " | ".join(x for x in (config.dono(FORNECEDOR), "assinatura") if x)
    if dados is None:
        return Resultado(sucesso=False, erro=erro_final, tentativas=tentativas,
                         temperatura_usada=NAO_SUPORTADO, dono_api=dono)

    u = dados.get("usage") or {}
    entrada = u.get("input_tokens") or 0
    saida = u.get("output_tokens") or 0
    cache_lido = u.get("cache_read_input_tokens") or 0
    cache_gravado = u.get("cache_creation_input_tokens") or 0
    modelos_usados = dados.get("modelUsage") or {}

    return Resultado(
        sucesso=True,
        texto=dados.get("result") or "",
        modelo_efetivo=next(iter(modelos_usados), modelo),
        id_resposta=dados.get("session_id") or "",
        tokens_entrada=entrada,
        tokens_saida=saida,
        tokens_cache=cache_lido,
        total_tokens=entrada + saida + cache_lido + cache_gravado,
        motivo_parada=dados.get("stop_reason") or "",
        latencia_ms=latencia,
        tentativas=tentativas,
        dono_api=dono,
        temperatura_usada=NAO_SUPORTADO,
    )


def listar_modelos() -> list[tuple[str, str]]:
    return list(ALIASES)
