"""Testes sem chamadas reais: provedores falsos e respostas HTTP simuladas."""

import json
import shutil
from pathlib import Path

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from executor import config, planilha, servico
from executor.provedores import PROVEDORES
from executor.provedores.base import Parametros, Resultado

RAIZ = Path(__file__).resolve().parent.parent
V1 = RAIZ / "planilha" / "PromptExecucoesSumario_v00001.xlsx"


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    shutil.copy(V1, tmp_path / V1.name)
    monkeypatch.setattr(config, "PASTA_PLANILHAS", tmp_path)
    monkeypatch.setattr(config, "chave", lambda f: "sk-teste-1234" if f in ("claude", "gemini") else "")
    monkeypatch.setattr(config, "dono", lambda f: "Diego")
    return tmp_path


@pytest.fixture
def falsos(monkeypatch):
    chamadas = []

    class Falso:
        def __init__(self, nome):
            self.nome = nome

        def executar(self, modelo, prompt, p):
            chamadas.append((self.nome, modelo))
            return Resultado(sucesso=True, texto=f"resposta de {modelo}", modelo_efetivo=modelo,
                             id_resposta="r1", tokens_entrada=10, tokens_saida=5, tokens_cache=0,
                             total_tokens=15, motivo_parada="end_turn", latencia_ms=100, tentativas=1,
                             temperatura_usada=p.temperatura, dono_api="Diego")

        def listar_modelos(self):
            return [(f"{self.nome}-modelo-a", "A"), (f"{self.nome}-modelo-b", "B")]

    for nome in ("claude", "gemini", "openai"):
        monkeypatch.setitem(PROVEDORES, nome, Falso(nome))
    return chamadas


def _abrir(pasta, versao):
    return load_workbook(pasta / f"PromptExecucoesSumario_v{versao:05d}.xlsx")


def test_execucao_gera_nova_versao_e_preserva_a_anterior(pasta, falsos):
    antes = (pasta / V1.name).read_bytes()
    r = servico.executar_lote({"claude": ["Haiku"]})
    assert r["planilha_gerada"] == "PromptExecucoesSumario_v00002.xlsx"
    assert (pasta / V1.name).read_bytes() == antes
    assert falsos == [("claude", "Haiku")]

    wb = _abrir(pasta, 2)
    execucoes = planilha.ler_execucoes(wb)
    assert [c.value for c in wb["Execuções"][1]][:23] == planilha.COLUNAS_EXECUCOES
    assert len(execucoes) == 2  # 1 migrada + 1 nova
    assert execucoes[0]["Fornecedor"] == "Gemini" and execucoes[0]["ID da execução"].startswith("migrado-")
    assert execucoes[1]["Modelo solicitado"] == "Haiku" and execucoes[1]["Status"] == "sucesso"
    assert execucoes[1]["Final da chave"] == "...1234"
    assert wb["Prompts"]["C2"].value == "Executado"


def test_execucoes_sao_acrescentadas_entre_versoes(pasta, falsos):
    servico.executar_lote({"claude": []})
    wb = _abrir(pasta, 2)
    wb["Prompts"]["C2"] = "Pronto para executar"
    wb.save(pasta / "PromptExecucoesSumario_v00002.xlsx")

    servico.executar_lote({"claude": []}, repeticoes=2)
    execucoes = planilha.ler_execucoes(_abrir(pasta, 3))
    assert len(execucoes) == 4  # migrada + 1 + 2


def test_sem_credencial_ou_fora_do_filtro_nao_executa_nem_versiona(pasta, falsos):
    r = servico.executar_lote({"openai": ["gpt-x"]})  # openai sem chave
    assert r["planilha_gerada"] is None
    assert {"fornecedor": "OpenAI", "motivo": "sem credencial no .env"} in r["ignorados"]

    r = servico.executar_lote({"claude": ["outro-modelo"]})  # linha pede Haiku
    assert r["planilha_gerada"] is None
    assert not falsos
    assert planilha.versoes(pasta)[-1][0] == 1


def test_modelo_fora_do_catalogo_vira_erro_sem_chamar_api(pasta, falsos):
    servico.atualizar_modelos("claude")
    r = servico.executar_lote({"claude": []})
    assert r["execucoes"][0]["status"] == "erro"
    assert "não consta na aba Modelos" in r["execucoes"][0]["erro"]
    assert not falsos
    wb = _abrir(pasta, 2)
    assert wb["Prompts"]["C2"].value == "Pronto para executar"  # tudo falhou: continua pronto


def test_catalogo_substitui_so_o_fornecedor(pasta, falsos):
    servico.atualizar_modelos("claude")
    servico.atualizar_modelos("gemini")
    servico.atualizar_modelos("anthropic")  # apelido de claude
    wb = _abrir(pasta, 1)  # catálogo grava na última versão, sem criar outra
    cat = planilha.ler_catalogo(wb)
    assert cat == {"claude": {"claude-modelo-a", "claude-modelo-b"},
                   "gemini": {"gemini-modelo-a", "gemini-modelo-b"}}


def test_sumario(pasta, falsos):
    servico.executar_lote({"claude": []}, repeticoes=3)
    ws = _abrir(pasta, 2)["Sumário"]
    linhas = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    assert ["Claude", 3, 0, 3] in [l[:4] for l in linhas]
    assert ["Gemini", 1, 0, 1] in [l[:4] for l in linhas]
    assert ["Claude", "Haiku", 3, 3, 0, 45, 100] in [l[:7] for l in linhas]


def test_api(pasta, falsos):
    import api

    cliente = TestClient(api.app)
    assert cliente.post("/execucoes", json={"modelos": {"xpto": []}}).status_code == 422
    r = cliente.post("/execucoes", json={"modelos": {"Claude": ["Haiku"]}, "temperatura": 0.7})
    assert r.status_code == 200 and r.json()["versao"] == 2
    assert cliente.get("/modelos/gemini").json()["quantidade"] == 2
    assert cliente.get("/modelos/openai").status_code == 422


# ---------------------------------------------------------------- parsing real dos SDKs

def _transporte(corpo: dict, headers: dict | None = None, status: int = 200):
    def handler(request):
        return httpx.Response(status, json=corpo, headers=headers or {})
    return httpx.MockTransport(handler)


def test_parsing_claude(monkeypatch):
    import anthropic
    from executor.provedores import claude

    corpo = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": "B"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 30,
                  "cache_creation_input_tokens": 5},
    }
    transporte = httpx2.MockTransport(
        lambda req: httpx2.Response(200, json=corpo, headers={"anthropic-organization-id": "org-9"}))
    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=transporte)))
    monkeypatch.setattr(config, "dono", lambda f: "Diego")
    r = claude.executar("claude-haiku-5-5", "oi", Parametros())
    assert r.sucesso and r.texto == "B" and r.id_resposta == "msg_1"
    assert (r.tokens_entrada, r.tokens_saida, r.tokens_cache, r.total_tokens) == (100, 20, 30, 155)
    assert r.dono_api == "Diego | org=org-9" and r.temperatura_usada == "não suportada"


def test_parsing_openai_e_temperatura_nao_suportada(monkeypatch):
    import openai
    from executor.provedores import openai_

    ok = {
        "id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-x-2026", "status": "completed",
        "output": [{"type": "message", "id": "m", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "A", "annotations": []}]}],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "usage": {"input_tokens": 50, "input_tokens_details": {"cached_tokens": 10},
                  "output_tokens": 7, "output_tokens_details": {"reasoning_tokens": 3}, "total_tokens": 57},
    }
    erro = {"error": {"message": "Unsupported parameter: 'temperature' is not supported with this model.",
                      "type": "invalid_request_error", "param": "temperature", "code": None}}
    pedidos = []

    def handler(request):
        corpo = json.loads(request.content)
        pedidos.append(corpo)
        if "temperature" in corpo:
            return httpx.Response(400, json=erro)
        return httpx.Response(200, json=ok, headers={"openai-organization": "org-x", "openai-project": "proj-y"})

    monkeypatch.setattr(openai_, "_cliente", lambda: openai.OpenAI(
        api_key="x", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(config, "dono", lambda f: "")
    r = openai_.executar("gpt-x", "oi", Parametros(temperatura=0))
    assert r.sucesso and r.texto == "A" and r.modelo_efetivo == "gpt-x-2026"
    assert (r.tokens_entrada, r.tokens_saida, r.tokens_cache, r.total_tokens) == (50, 7, 10, 57)
    assert r.temperatura_usada == "não suportada" and r.tentativas == 2
    assert r.dono_api == "org=org-x | projeto=proj-y"
    assert len(pedidos) == 2


def test_parsing_gemini(monkeypatch):
    from google import genai
    from google.genai import types
    from executor.provedores import gemini

    corpo = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "B"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 9, "totalTokenCount": 60,
                          "thoughtsTokenCount": 11},
        "modelVersion": "gemini-3.5-flash-lite", "responseId": "abc",
    }
    monkeypatch.setattr(gemini, "_cliente", lambda: genai.Client(
        api_key="x", http_options=types.HttpOptions(httpx_client=httpx.Client(transport=_transporte(corpo)))))
    r = gemini.executar("gemini-3.5-flash-lite", "oi", Parametros())
    assert r.sucesso and r.texto == "B" and r.id_resposta == "abc" and r.motivo_parada == "STOP"
    assert (r.tokens_entrada, r.tokens_saida, r.total_tokens) == (40, 9, 60)


def test_retentativa_em_503(monkeypatch):
    import anthropic
    from executor.provedores import base, claude

    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    estado = {"n": 0}
    ok = {"id": "msg_2", "type": "message", "role": "assistant", "model": "m", "content": [],
          "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}

    def handler(request):
        estado["n"] += 1
        if estado["n"] < 3:
            return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
        return httpx2.Response(200, json=ok)

    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    r = claude.executar("m", "oi", Parametros())
    assert r.sucesso and r.tentativas == 3


# ---------------------------------------------------------------- Claude pela assinatura

def _saida_cli(**extra):
    base = {
        "type": "result", "subtype": "success", "is_error": False, "result": "B", "session_id": "sess-1",
        "stop_reason": "end_turn", "terminal_reason": "completed",
        "usage": {"input_tokens": 12, "output_tokens": 4, "cache_read_input_tokens": 100,
                  "cache_creation_input_tokens": 8},
        "modelUsage": {"claude-haiku-5-5": {"inputTokens": 12}},
    }
    base.update(extra)
    return json.dumps(base)


def _fake_run(saidas, chamadas):
    import subprocess

    def run(cmd, **kwargs):
        chamadas.append((cmd, kwargs))
        saida = saidas.pop(0)
        if isinstance(saida, Exception):
            raise saida
        return subprocess.CompletedProcess(cmd, 0, stdout=saida, stderr="")
    return run


def test_claude_code_sucesso(monkeypatch):
    from executor.provedores import claude_code

    chamadas = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-nao-pode-ir-para-o-cli")
    monkeypatch.setattr(claude_code.subprocess, "run", _fake_run([_saida_cli()], chamadas))
    monkeypatch.setattr(config, "dono", lambda f: "Diego")
    r = claude_code.executar("haiku", "oi", Parametros(system_prompt="seja breve"))

    cmd, kw = chamadas[0]
    assert cmd[:2] == ["claude", "-p"] and "--model" in cmd and cmd[cmd.index("--model") + 1] == "haiku"
    assert "--append-system-prompt" in cmd and "--disallowedTools" in cmd
    assert kw["input"] == "oi" and "ANTHROPIC_API_KEY" not in kw["env"]
    assert r.sucesso and r.texto == "B" and r.id_resposta == "sess-1" and r.modelo_efetivo == "claude-haiku-5-5"
    assert (r.tokens_entrada, r.tokens_saida, r.tokens_cache, r.total_tokens) == (12, 4, 100, 124)
    assert r.dono_api == "Diego | assinatura" and r.temperatura_usada == "não suportada"


def test_claude_code_erro_de_autenticacao_nao_repete(monkeypatch):
    from executor.provedores import claude_code

    chamadas = []
    erro = _saida_cli(is_error=True, result="Authentication error", terminal_reason="api_error",
                      api_error_status=None)
    monkeypatch.setattr(claude_code.subprocess, "run", _fake_run([erro], chamadas))
    r = claude_code.executar("haiku", "oi", Parametros())
    assert not r.sucesso and "Authentication error" in r.erro and r.tentativas == 1 and len(chamadas) == 1


def test_claude_code_repete_em_429_e_timeout(monkeypatch):
    import subprocess
    from executor.provedores import base, claude_code

    monkeypatch.setattr(claude_code.time, "sleep", lambda s: None)
    chamadas = []
    limite = _saida_cli(is_error=True, result="rate limit", terminal_reason="api_error", api_error_status=429)
    saidas = [subprocess.TimeoutExpired("claude", 1), limite, _saida_cli()]
    monkeypatch.setattr(claude_code.subprocess, "run", _fake_run(saidas, chamadas))
    r = claude_code.executar("haiku", "oi", Parametros())
    assert r.sucesso and r.tentativas == 3


def test_claude_code_no_fluxo(pasta, monkeypatch):
    from executor.provedores import claude_code

    chamadas = []
    monkeypatch.setattr(config, "tem_credencial", lambda f: True)
    monkeypatch.setattr(claude_code.subprocess, "run", _fake_run([_saida_cli()], chamadas))

    # linha pede Claude/Haiku; a requisição só tem a assinatura
    r = servico.executar_lote({"assinatura": ["haiku"]})
    assert r["execucoes"][0]["fornecedor"] == "Claude (assinatura)" and r["execucoes"][0]["status"] == "sucesso"
    assert chamadas[0][0][chamadas[0][0].index("--model") + 1] == "haiku"
    wb = _abrir(pasta, 2)
    ex = planilha.ler_execucoes(wb)[-1]
    assert ex["Fornecedor"] == "Claude (assinatura)" and ex["Final da chave"] in (None, "")
    assert ex["Modelo efetivo"] == "claude-haiku-5-5" and ex["Total de tokens"] == 124

    # com "claude" (API) na requisição, a linha continua indo para a API
    wb = _abrir(pasta, 2)
    wb["Prompts"]["C2"] = "Pronto para executar"
    wb.save(pasta / "PromptExecucoesSumario_v00002.xlsx")
    monkeypatch.setitem(PROVEDORES, "claude", type("F", (), {"executar": staticmethod(
        lambda m, p_, par: Resultado(sucesso=True, texto="x", modelo_efetivo=m))})())
    r = servico.executar_lote({"claude": [], "assinatura": []})
    assert [e["fornecedor"] for e in r["execucoes"]] == ["Claude"]
