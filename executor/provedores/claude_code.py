"""Claude pela assinatura: executa `claude -p` (Claude Code) logado na conta do plano.

Não usa chave de API. As execuções consomem o limite de uso da assinatura.
Isolamento da chamada, para o resultado não ser contaminado pelo ambiente de programação:
- o system prompt padrão do Claude Code (assistente de programação, com diretório, git e
  CLAUDE.md) é SUBSTITUÍDO pelo da linha ou por CLAUDE_CODE_SYSTEM_PROMPT_PADRAO;
- roda num diretório temporário vazio, sem ferramentas, sem MCP e sem arquivos de configuração.

Limites que continuam: não há controle de temperatura nem de máximo de tokens, e o modelo
pode usar raciocínio estendido (registrado em "Tokens de raciocínio").
"""

import json
import os
import subprocess
import tempfile
import time

from executor import config
from executor.provedores.base import Parametros, Resultado

FORNECEDOR = "claude_code"
NAO_SUPORTADO = "não suportada"
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


def _system_prompt(p: Parametros) -> str:
    return p.system_prompt or config.CLAUDE_CODE_SYSTEM_PROMPT_PADRAO


def _comando(modelo: str, p: Parametros) -> list[str]:
    return [
        config.CLAUDE_CODE_BIN, "-p",
        "--output-format", "json",
        "--no-session-persistence",
        "--model", modelo,
        "--system-prompt", _system_prompt(p),  # substitui o prompt de programação
        "--tools", "",                         # nenhuma ferramenta
        "--strict-mcp-config",                 # nenhum servidor MCP
        "--setting-sources", "",               # ignora configurações de usuário/projeto
    ]


def _uma_chamada(modelo: str, prompt: str, p: Parametros) -> dict:
    try:
        # diretório vazio: sem repositório, git status nem CLAUDE.md no contexto
        with tempfile.TemporaryDirectory(prefix="executor_prompts_") as vazio:
            proc = subprocess.run(
                _comando(modelo, p), input=prompt, capture_output=True, text=True, encoding="utf-8",
                timeout=config.CLAUDE_CODE_TIMEOUT, env=_ambiente(), cwd=vazio,
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
                         temperatura_usada=NAO_SUPORTADO, dono_api=dono,
                         system_prompt_usado=_system_prompt(p))

    u = dados.get("usage") or {}
    entrada = u.get("input_tokens") or 0
    saida = u.get("output_tokens") or 0
    cache_lido = u.get("cache_read_input_tokens") or 0
    cache_gravado = u.get("cache_creation_input_tokens") or 0
    modelos_usados = dados.get("modelUsage") or {}
    raciocinio = (u.get("output_tokens_details") or {}).get("thinking_tokens")

    return Resultado(
        sucesso=True,
        texto=dados.get("result") or "",
        modelo_efetivo=next(iter(modelos_usados), modelo),
        id_resposta=dados.get("session_id") or "",
        # input_tokens do Claude exclui o cache: soma para ter a entrada total
        tokens_entrada=entrada + cache_lido + cache_gravado,
        tokens_saida=saida,
        tokens_cache=cache_lido,
        tokens_raciocinio=raciocinio,
        turnos=dados.get("num_turns"),
        total_tokens=entrada + saida + cache_lido + cache_gravado,
        motivo_parada=dados.get("stop_reason") or "",
        latencia_ms=latencia,
        tentativas=tentativas,
        dono_api=dono,
        temperatura_usada=NAO_SUPORTADO,
        system_prompt_usado=_system_prompt(p),
    )


def listar_modelos() -> list[tuple[str, str]]:
    return list(ALIASES)
