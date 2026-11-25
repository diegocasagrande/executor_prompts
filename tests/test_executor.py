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
# cópia fixa da planilha original, independente das versões de trabalho em planilha/
V1 = RAIZ / "tests" / "dados" / "PromptExecucoesSumario_v00001.xlsx"


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
            self.ultimo_system = p.system_prompt
            return Resultado(sucesso=True, texto=f"resposta de {modelo}", modelo_efetivo=modelo,
                             id_resposta="r1", tokens_entrada=10, tokens_saida=5, tokens_cache=0,
                             total_tokens=15, motivo_parada="end_turn", latencia_ms=100, tentativas=1,
                             temperatura_usada=p.temperatura, dono_api="Diego")

        def listar_modelos(self):
            return [(f"{self.nome}-modelo-a", "A"), (f"{self.nome}-modelo-b", "B")]

    for nome in ("claude", "gemini", "openai"):
        monkeypatch.setitem(PROVEDORES, nome, Falso(nome))
    return chamadas


def execucoes_texto(wb):
    return [(r[0], r[1]) for r in wb["Execuções"].iter_rows(min_row=2, values_only=True) if r[0]]


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
    assert [c.value for c in wb["Execuções"][1]][:len(planilha.COLUNAS_EXECUCOES)] == planilha.COLUNAS_EXECUCOES
    # A e B são o identificador do prompt e o texto do resultado, como na planilha original
    assert [c.value for c in wb["Execuções"][1]][:2] == ["Identificador Único", "Texto do Resultado"]
    assert execucoes_texto(wb)[-1][0] == "PROMPT_D_V1" and execucoes_texto(wb)[-1][1] == "resposta de Haiku"
    assert len(execucoes) == 2  # 1 migrada + 1 nova
    assert execucoes[0]["Fornecedor"] == "Gemini" and execucoes[0]["ID da execução"].startswith("migrado-")
    assert execucoes[0]["Identificador Único"] == "PROMPT_D_V1" and execucoes[0]["Modelo utilizado"] == "gemini-3.5-flash-lite"
    assert execucoes[0]["Data da Execução"] == "16/11/2026" and execucoes[0]["Hora da Execução"] == "05:13:09"
    assert execucoes[1]["Modelo solicitado"] == "Haiku" and execucoes[1]["Modelo utilizado"] == "Haiku" and execucoes[1]["Status"] == "sucesso"
    assert execucoes[1]["Final da chave"] == "...1234"
    assert wb["Prompts"]["C2"].value == "Executado"


def test_execucoes_sao_acrescentadas_entre_versoes(pasta, falsos):
    servico.executar_lote({"claude": []})
    wb = _abrir(pasta, 2)
    wb["Prompts"]["C2"] = "Pronto para executar"
    wb.save(pasta / "PromptExecucoesSumario_v00002.xlsx")

    r = servico.executar_lote({"claude": []}, repeticoes=2)
    execucoes = planilha.ler_execucoes(_abrir(pasta, 3))
    assert len(execucoes) == 4  # migrada + 1 + 2
    # cada execução marca a repetição e o lote
    assert [(e["Nº da repetição"], e["Total de repetições"]) for e in execucoes[2:]] == [(1, 2), (2, 2)]
    assert execucoes[2]["ID do lote"] == execucoes[3]["ID do lote"] == r["lote"] != execucoes[1]["ID do lote"]
    assert [e["repeticao"] for e in r["execucoes"]] == ["1/2", "2/2"]


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


def _sse_claude(corpo: dict) -> bytes:
    """Converte uma resposta da Messages API no fluxo SSE equivalente (o provedor usa streaming)."""
    def ev(tipo, dados):
        return f"event: {tipo}\ndata: {json.dumps(dados)}\n\n"

    inicio = dict(corpo, content=[], stop_reason=None, stop_sequence=None)
    partes = [ev("message_start", {"type": "message_start", "message": inicio})]
    for i, bloco in enumerate(corpo["content"]):
        partes.append(ev("content_block_start", {"type": "content_block_start", "index": i,
                                                 "content_block": {"type": "text", "text": ""}}))
        partes.append(ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                                 "delta": {"type": "text_delta", "text": bloco["text"]}}))
        partes.append(ev("content_block_stop", {"type": "content_block_stop", "index": i}))
    partes.append(ev("message_delta", {"type": "message_delta",
                                       "delta": {"stop_reason": corpo["stop_reason"], "stop_sequence": None},
                                       "usage": {"output_tokens": corpo["usage"]["output_tokens"]}}))
    partes.append(ev("message_stop", {"type": "message_stop"}))
    return "".join(partes).encode()


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
        lambda req: httpx2.Response(200, content=_sse_claude(corpo),
                                    headers={"anthropic-organization-id": "org-9",
                                             "content-type": "text/event-stream"}))
    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=transporte)))
    monkeypatch.setattr(config, "dono", lambda f: "Diego")
    r = claude.executar("claude-haiku-5-5", "oi", Parametros())
    assert r.sucesso and r.texto == "B" and r.id_resposta == "msg_1"
    assert (r.tokens_entrada, r.tokens_saida, r.tokens_cache, r.total_tokens) == (135, 20, 30, 155)
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
    assert r.tokens_raciocinio == 3
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
    assert r.tokens_raciocinio == 11


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
        return httpx2.Response(200, content=_sse_claude(ok), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    r = claude.executar("m", "oi", Parametros())
    assert r.sucesso and r.tentativas == 3


# ---------------------------------------------------------------- Claude pela assinatura

def _saida_cli(**extra):
    base = {
        "type": "result", "subtype": "success", "is_error": False, "result": "B", "session_id": "sess-1",
        "stop_reason": "end_turn", "terminal_reason": "completed",
        "num_turns": 1,
        "usage": {"input_tokens": 12, "output_tokens": 4, "cache_read_input_tokens": 100,
                  "cache_creation_input_tokens": 8, "output_tokens_details": {"thinking_tokens": 3}},
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
    assert cmd[cmd.index("--system-prompt") + 1] == "seja breve"
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--append-system-prompt" not in cmd
    assert kw["input"] == "oi" and "ANTHROPIC_API_KEY" not in kw["env"]
    # roda fora do repositório, num diretório temporário
    from pathlib import Path as _P
    assert _P(kw["cwd"]).name.startswith("executor_prompts_") and _P(kw["cwd"]) != config.RAIZ
    assert r.sucesso and r.texto == "B" and r.id_resposta == "sess-1" and r.modelo_efetivo == "claude-haiku-5-5"
    # entrada total = input + cache lido + cache gravado
    assert (r.tokens_entrada, r.tokens_saida, r.tokens_cache, r.total_tokens) == (120, 4, 100, 124)
    assert r.tokens_raciocinio == 3 and r.turnos == 1 and r.system_prompt_usado == "seja breve"
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
    assert ex["Modelo utilizado"] == "claude-haiku-5-5" and ex["Total de tokens"] == 124

    # com "claude" (API) na requisição, a linha continua indo para a API
    wb = _abrir(pasta, 2)
    wb["Prompts"]["C2"] = "Pronto para executar"
    wb.save(pasta / "PromptExecucoesSumario_v00002.xlsx")
    monkeypatch.setitem(PROVEDORES, "claude", type("F", (), {"executar": staticmethod(
        lambda m, p_, par: Resultado(sucesso=True, texto="x", modelo_efetivo=m))})())
    r = servico.executar_lote({"claude": [], "assinatura": []})
    assert [e["fornecedor"] for e in r["execucoes"]] == ["Claude"]


def test_system_prompt_vem_da_coluna_f_da_aba_prompts(pasta, falsos, monkeypatch):
    vistos = []
    original = PROVEDORES["claude"].executar

    def espiao(modelo, prompt, p):
        vistos.append(p.system_prompt)
        return original(modelo, prompt, p)

    monkeypatch.setattr(PROVEDORES["claude"], "executar", espiao)

    # linha sem system prompt: nada é enviado
    servico.executar_lote({"claude": []})
    wb = _abrir(pasta, 2)
    assert wb["Prompts"]["F1"].value == "System prompt"
    assert planilha.ler_execucoes(wb)[-1]["System prompt"] in (None, "")

    # linha com system prompt
    wb["Prompts"]["C2"] = "Pronto para executar"
    wb["Prompts"]["F2"] = "seja breve"
    wb.save(pasta / "PromptExecucoesSumario_v00002.xlsx")
    servico.executar_lote({"claude": []})
    assert vistos == [None, "seja breve"]
    assert planilha.ler_execucoes(_abrir(pasta, 3))[-1]["System prompt"] == "seja breve"


def test_api_nao_aceita_system_prompt_na_requisicao():
    import api

    assert "system_prompt" not in api.RequisicaoExecucao.model_fields


def test_claude_code_sem_system_prompt_na_linha_usa_o_neutro(monkeypatch):
    from executor.provedores import claude_code

    chamadas = []
    monkeypatch.setattr(claude_code.subprocess, "run", _fake_run([_saida_cli()], chamadas))
    r = claude_code.executar("haiku", "oi", Parametros())
    cmd = chamadas[0][0]
    assert cmd[cmd.index("--system-prompt") + 1] == config.CLAUDE_CODE_SYSTEM_PROMPT_PADRAO
    assert r.system_prompt_usado == config.CLAUDE_CODE_SYSTEM_PROMPT_PADRAO


def test_migracao_preserva_linhas_do_layout_de_23_colunas(pasta, falsos):
    """Planilhas geradas antes das colunas de raciocínio/turnos continuam válidas."""
    servico.executar_lote({"claude": []})
    wb = _abrir(pasta, 2)
    ws = wb["Execuções"]
    ws.delete_cols(len(planilha.COLUNAS_EXECUCOES) - 1, 2)  # simula o layout antigo
    ws["C2"].value  # noqa: B018
    wb["Prompts"]["C2"] = "Pronto para executar"
    wb.save(pasta / "PromptExecucoesSumario_v00002.xlsx")
    servico.executar_lote({"claude": []})
    ex = planilha.ler_execucoes(_abrir(pasta, 3))
    assert len(ex) == 3 and ex[1]["Texto do Resultado"] == "resposta de Haiku" and ex[1]["Tokens de entrada"] == 10
    assert ex[1]["Mensagem de erro"] in (None, "")  # execução real não vira "migrada"


def test_log_mostra_progresso_metadados_e_resposta(pasta, falsos, monkeypatch, tmp_path, capsys):
    import logging
    from executor import log as executor_log

    monkeypatch.setattr(executor_log, "PASTA_LOGS", tmp_path / "logs")
    monkeypatch.setattr(executor_log, "RESPOSTA_TERMINAL", 5)
    monkeypatch.setattr(executor_log, "_configurado", False)
    for nome in ("executor", "executor.resposta.terminal", "executor.resposta.arquivo"):
        logging.getLogger(nome).handlers.clear()
    executor_log.configurar()

    servico.executar_lote({"claude": []}, repeticoes=2)
    for h in logging.getLogger("executor").handlers:
        h.flush()

    terminal = capsys.readouterr().out
    arquivo = (tmp_path / "logs" / "executor.log").read_text(encoding="utf-8")
    assert "[1/2] PROMPT_D_V1 | Claude | Haiku | repetição 1/2 | início" in terminal
    assert "[2/2] PROMPT_D_V1 (rep. 2/2) | Claude | Haiku | SUCESSO" in terminal
    assert "tokens entrada=10 saída=5" in terminal and "latência=100 ms" in terminal
    assert "    respo… [+12 caracteres]" in terminal          # cortada no terminal
    assert "    resposta de Haiku" in arquivo                   # completa no arquivo
    assert "=== Fim do lote | PromptExecucoesSumario_v00002.xlsx | 2 chamadas | 2 sucesso | 0 erro" in arquivo
    for nome in ("executor", "executor.resposta.terminal", "executor.resposta.arquivo"):
        logging.getLogger(nome).handlers.clear()


# ---------------------------------------------------------------- isolamento entre execuções
# Duas chamadas seguidas no mesmo processo: a segunda requisição não pode conter nada da primeira.

PRIMEIRO, SEGUNDO = "Memorize a palavra-código ABACAXI-7391.", "Qual palavra-código eu pedi?"


def _sem_vazamento(corpo: str):
    assert "ABACAXI" not in corpo and "Memorize" not in corpo


def test_isolamento_claude_api(monkeypatch):
    import anthropic
    from executor.provedores import claude

    corpos = []
    ok = {"id": "m", "type": "message", "role": "assistant", "model": "m", "content": [{"type": "text", "text": "x"}],
          "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}

    def handler(req):
        corpos.append(req.content.decode())
        return httpx2.Response(200, content=_sse_claude(ok), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    claude.executar("m", PRIMEIRO, Parametros())
    claude.executar("m", SEGUNDO, Parametros())
    segundo = json.loads(corpos[1])
    assert segundo["messages"] == [{"role": "user", "content": SEGUNDO}]
    _sem_vazamento(corpos[1])


def test_isolamento_openai(monkeypatch):
    import openai
    from executor.provedores import openai_

    corpos = []
    ok = {"id": "resp_1", "object": "response", "created_at": 1, "model": "g", "status": "completed",
          "output": [], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
          "usage": {"input_tokens": 1, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1,
                    "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 2}}

    def handler(req):
        corpos.append(req.content.decode())
        return httpx.Response(200, json=ok)

    monkeypatch.setattr(openai_, "_cliente", lambda: openai.OpenAI(
        api_key="x", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    openai_.executar("g", PRIMEIRO, Parametros())
    openai_.executar("g", SEGUNDO, Parametros())
    segundo = json.loads(corpos[1])
    assert segundo["input"] == SEGUNDO
    assert "previous_response_id" not in segundo and "conversation" not in segundo
    _sem_vazamento(corpos[1])


def test_isolamento_gemini(monkeypatch):
    from google import genai
    from google.genai import types
    from executor.provedores import gemini

    corpos = []
    ok = {"candidates": [{"content": {"role": "model", "parts": [{"text": "x"}]}, "finishReason": "STOP"}],
          "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2}}

    def handler(req):
        corpos.append(req.content.decode())
        return httpx.Response(200, json=ok)

    monkeypatch.setattr(gemini, "_cliente", lambda: genai.Client(
        api_key="x", http_options=types.HttpOptions(httpx_client=httpx.Client(transport=httpx.MockTransport(handler)))))
    gemini.executar("g", PRIMEIRO, Parametros())
    gemini.executar("g", SEGUNDO, Parametros())
    segundo = json.loads(corpos[1])
    assert len(segundo["contents"]) == 1 and segundo["contents"][0]["parts"] == [{"text": SEGUNDO}]
    _sem_vazamento(corpos[1])


def test_isolamento_claude_code(monkeypatch):
    from pathlib import Path as _P
    from executor.provedores import claude_code

    chamadas, pastas = [], []

    def run(cmd, **kw):
        chamadas.append((cmd, kw))
        pastas.append((kw["cwd"], _P(kw["cwd"]).exists(), list(_P(kw["cwd"]).iterdir())))
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, stdout=_saida_cli(), stderr="")

    monkeypatch.setattr(claude_code.subprocess, "run", run)
    claude_code.executar("haiku", PRIMEIRO, Parametros())
    claude_code.executar("haiku", SEGUNDO, Parametros())

    (cmd1, kw1), (cmd2, kw2) = chamadas
    assert kw2["input"] == SEGUNDO and cmd1 == cmd2           # mesmo comando; só o stdin muda
    _sem_vazamento(" ".join(cmd2) + kw2["input"])
    proibidos = {"--continue", "-c", "--resume", "-r", "--session-id", "--fork-session", "--add-dir"}
    assert not proibidos & set(cmd2)
    assert "--no-session-persistence" in cmd2
    assert pastas[0][0] != pastas[1][0]                        # pasta nova a cada chamada
    assert all(existe and conteudo == [] for _, existe, conteudo in pastas)  # vazia durante a chamada
    assert not _P(pastas[0][0]).exists()                       # apagada ao terminar


def test_isolamento_no_lote_cada_chamada_recebe_so_o_proprio_prompt(pasta, monkeypatch):
    """Lote com dois prompts: cada provedor recebe só o texto e o system prompt da própria linha."""
    recebidos = []

    class Espiao:
        def executar(self, modelo, prompt, p):
            recebidos.append((prompt, p.system_prompt))
            return Resultado(sucesso=True, texto="ok", modelo_efetivo=modelo)

    monkeypatch.setitem(PROVEDORES, "claude", Espiao())
    wb = load_workbook(pasta / V1.name)
    ws = wb["Prompts"]
    ws.append(["P2", SEGUNDO, "Pronto para executar", "Claude", "Haiku"])
    ws["B2"] = PRIMEIRO
    ws["F1"], ws["F2"] = "System prompt", "system da linha 2"
    wb.save(pasta / V1.name)

    servico.executar_lote({"claude": []})
    assert recebidos == [(PRIMEIRO, "system da linha 2"), (SEGUNDO, None)]


def test_claude_max_tokens_alto_usa_streaming(monkeypatch):
    """Com max_tokens acima de ~21 mil, o SDK da Anthropic recusa chamadas sem streaming."""
    import anthropic
    from executor.provedores import claude

    enviado = {}
    corpo = {"id": "msg_3", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
             "content": [{"type": "text", "text": "soneto"}], "stop_reason": "end_turn", "stop_sequence": None,
             "usage": {"input_tokens": 10, "output_tokens": 25000}}

    def handler(request):
        enviado.update(json.loads(request.content))
        return httpx2.Response(200, content=_sse_claude(corpo), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    r = claude.executar("claude-sonnet-5-5", "oi", Parametros(max_tokens=32000))
    assert r.sucesso and r.texto == "soneto" and r.tokens_saida == 25000
    assert enviado["max_tokens"] == 32000 and enviado["stream"] is True


def test_claude_streaming_repete_erro_de_sobrecarga_no_meio_do_fluxo(monkeypatch):
    """Com streaming, a sobrecarga pode chegar como evento 'error' depois do HTTP 200."""
    import anthropic
    from executor.provedores import base, claude

    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    inicio = {"id": "m", "type": "message", "role": "assistant", "model": "m", "content": [],
              "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}
    erro = (f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': inicio})}\n\n"
            'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n').encode()
    ok = {"id": "msg_4", "type": "message", "role": "assistant", "model": "m",
          "content": [{"type": "text", "text": "fim"}], "stop_reason": "end_turn", "stop_sequence": None,
          "usage": {"input_tokens": 1, "output_tokens": 1}}
    n = {"c": 0}

    def handler(request):
        n["c"] += 1
        conteudo = erro if n["c"] < 3 else _sse_claude(ok)
        return httpx2.Response(200, content=conteudo, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    r = claude.executar("m", "oi", Parametros())
    assert r.sucesso and r.texto == "fim" and r.tentativas == 3


def test_claude_streaming_erro_definitivo_no_fluxo_nao_repete(monkeypatch):
    import anthropic
    from executor.provedores import claude

    erro = b'event: error\ndata: {"type":"error","error":{"type":"invalid_request_error","message":"x"}}\n\n'
    n = {"c": 0}

    def handler(request):
        n["c"] += 1
        return httpx2.Response(200, content=erro, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(claude, "_cliente", lambda: anthropic.Anthropic(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    r = claude.executar("m", "oi", Parametros())
    assert not r.sucesso and r.tentativas == 1 and n["c"] == 1
