"""Diagnóstico do fornecedor claude_code (Claude pela assinatura, via `claude -p`).

Roda, no mesmo ambiente Python do executor, cada etapa que o executor depende e para
na primeira que falha, com a mensagem de erro do CLI e a sugestão de correção.

Uso, na raiz do projeto e com o mesmo terminal/venv onde roda o uvicorn:
    python scripts/diagnostico_claude_code.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from executor import config  # noqa: E402
from executor.provedores import claude_code  # noqa: E402
from executor.provedores.base import Parametros  # noqa: E402

PROMPT = "Responda apenas: ok"


def etapa(titulo):
    print(f"\n=== {titulo}")


def rodar(cmd, entrada=None, env=None):
    with tempfile.TemporaryDirectory() as vazio:
        try:
            return subprocess.run(cmd, input=entrada, capture_output=True, text=True, encoding="utf-8",
                                  timeout=180, env=env or claude_code._ambiente(), cwd=vazio)
        except subprocess.TimeoutExpired:
            print("  FALHA: tempo esgotado (180 s)")
            return None


def ler_json(proc):
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None


def falha(msg, dica):
    print(f"  FALHA: {msg}")
    print(f"  O QUE FAZER: {dica}")
    sys.exit(1)


def main():
    etapa("1. Ambiente")
    print(f"  Python: {sys.executable}")
    print(f"  CLAUDE_CODE_BIN: {config.CLAUDE_CODE_BIN!r}")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "HOME"):
        valor = os.environ.get(var)
        if valor:
            mostrado = valor if var in ("HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_BASE_URL") else f"definida (...{valor[-4:]})"
            print(f"  {var}: {mostrado}")
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("  (o executor remove ANTHROPIC_API_KEY da chamada ao claude -p, para usar a assinatura)")

    etapa("2. CLI encontrado no PATH deste processo")
    caminho = shutil.which(config.CLAUDE_CODE_BIN)
    if not caminho:
        falha(f"'{config.CLAUDE_CODE_BIN}' não está no PATH do Python que roda o executor. "
              f"PATH atual: {os.environ.get('PATH')}",
              "rode `which claude` no terminal onde o CLI funciona e coloque o caminho completo em "
              "CLAUDE_CODE_BIN no .env (ex.: /home/SEU_USUARIO/.local/bin/claude), ou suba o uvicorn "
              "nesse mesmo terminal. Sem isso, o executor ignora o fornecedor.")
    print(f"  OK: {caminho}")
    proc = rodar([caminho, "--version"])
    print(f"  versão: {(proc.stdout or proc.stderr).strip() if proc else '?'}")

    etapa("3. Login (claude auth status)")
    proc = rodar([caminho, "auth", "status", "--json"])
    status = ler_json(proc) if proc else None
    if status is None:
        print(f"  não foi possível ler o status: {(proc.stdout or proc.stderr)[:300] if proc else ''}")
    else:
        print(f"  loggedIn={status.get('loggedIn')} | authMethod={status.get('authMethod')} | "
              f"expira={status.get('loginExpiresAt')} | config={status.get('configDirectory')}")
        if not status.get("loggedIn"):
            falha("o CLI não está logado neste usuário/ambiente.",
                  "rode `claude` (ou `claude auth login`) neste mesmo terminal e entre com a conta do plano. "
                  "Se já fez login, confira se foi no mesmo usuário do WSL que roda o uvicorn.")
        if status.get("authMethod") not in (None, "claude.ai", "oauth", "subscription") and "api" in str(status.get("authMethod")).lower():
            print("  ATENÇÃO: o login parece ser por chave de API, não pela assinatura.")

    # Etapas 4 a 6: do comando mais simples ao comando exato do executor
    base = [caminho, "-p", "--output-format", "json", "--no-session-persistence"]
    exato = claude_code._comando("haiku", Parametros())
    exato[0] = caminho
    testes = [
        ("4. Chamada mínima (claude -p)", base),
        ("5. Com o modelo haiku", base + ["--model", "haiku"]),
        ("6. Comando exato do executor (system prompt próprio, sem ferramentas, sem MCP, sem configurações)", exato),
    ]
    for titulo, cmd in testes:
        etapa(titulo)
        proc = rodar(cmd, entrada=PROMPT)
        if proc is None:
            falha("o CLI não respondeu.", "verifique a conexão e se `claude -p \"ok\"` responde no terminal.")
        dados = ler_json(proc)
        if not isinstance(dados, dict):
            falha(f"saída não é JSON (código {proc.returncode}). stdout: {proc.stdout[:400]!r} stderr: {proc.stderr[:400]!r}",
                  "a versão do CLI pode ter mudado o formato; envie esta saída.")
        if dados.get("is_error"):
            msg = f"{dados.get('terminal_reason')}: {dados.get('result')} (status HTTP {dados.get('api_error_status')})"
            texto = str(dados.get("result")).lower()
            if "auth" in texto or "login" in texto or "credential" in texto:
                dica = "refaça o login com `claude auth login` neste terminal (o token pode ter expirado)."
            elif "limit" in texto or "usage" in texto or dados.get("api_error_status") == 429:
                dica = "limite de uso da assinatura atingido; aguarde a renovação indicada pelo CLI."
            elif "model" in texto:
                dica = "o modelo não está disponível no seu plano; teste com sonnet ou opus."
            else:
                dica = "envie esta saída; se só esta etapa falha, a causa é uma das opções acrescentadas nela."
            falha(msg, dica)
        print(f"  OK: resposta={str(dados.get('result'))[:60]!r} | modelo={next(iter(dados.get('modelUsage') or {}), '?')} "
              f"| tokens entrada={dados.get('usage', {}).get('input_tokens')}")

    etapa("7. Provedor do executor (claude_code.executar)")
    r = claude_code.executar("haiku", PROMPT, Parametros())
    if not r.sucesso:
        falha(r.erro, "envie esta saída.")
    print(f"  OK: {r.texto[:60]!r} | modelo utilizado={r.modelo_efetivo} | tokens entrada={r.tokens_entrada}")
    print("\nTudo certo: o executor consegue usar o Claude pela assinatura neste ambiente.")


if __name__ == "__main__":
    main()
