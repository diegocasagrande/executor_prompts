"""Fornecedor Gemini (Google Gen AI SDK)."""

from google import genai
from google.genai import errors, types

from executor import config
from executor.provedores.base import Parametros, Resultado, com_retentativas, rejeita_temperatura

FORNECEDOR = "gemini"
TRANSITORIOS = {408, 429, 500, 502, 503, 504}


def _cliente():
    return genai.Client(api_key=config.chave(FORNECEDOR))


def _eh_transitorio(erro):
    return isinstance(erro, errors.APIError) and getattr(erro, "code", None) in TRANSITORIOS


def _nome(valor) -> str:
    if valor is None:
        return ""
    return getattr(valor, "name", None) or str(valor)


def executar(modelo: str, prompt: str, p: Parametros) -> Resultado:
    cliente = _cliente()
    temperatura = p.temperatura
    tentativas = 0

    def chamada():
        cfg = types.GenerateContentConfig(
            max_output_tokens=p.max_tokens,
            temperature=temperatura,
            system_instruction=p.system_prompt or None,
        )
        return cliente.models.generate_content(model=modelo, contents=prompt, config=cfg)

    try:
        try:
            resp, tentativas, latencia = com_retentativas(chamada, _eh_transitorio, config.MAX_TENTATIVAS)
        except errors.ClientError as erro:
            if temperatura is None or not rejeita_temperatura(erro):
                raise
            tentativas = erro.tentativas
            temperatura = None
            resp, extra, latencia = com_retentativas(chamada, _eh_transitorio, config.MAX_TENTATIVAS)
            tentativas += extra
    except Exception as erro:  # noqa: BLE001
        return Resultado(
            sucesso=False,
            erro=f"{type(erro).__name__}: {erro}",
            tentativas=getattr(erro, "tentativas", tentativas or 1),
            temperatura_usada=temperatura if temperatura is not None else "não suportada",
            dono_api=config.dono(FORNECEDOR),
        )

    u = resp.usage_metadata
    if resp.candidates:
        motivo = _nome(resp.candidates[0].finish_reason)
    elif resp.prompt_feedback and resp.prompt_feedback.block_reason:
        motivo = f"BLOQUEADO: {_nome(resp.prompt_feedback.block_reason)}"
    else:
        motivo = ""

    try:
        texto = resp.text or ""
    except Exception:  # noqa: BLE001 - .text falha quando o conteúdo foi bloqueado
        texto = ""

    sucesso = bool(resp.candidates) and not motivo.startswith("BLOQUEADO")
    return Resultado(
        sucesso=sucesso,
        texto=texto,
        modelo_efetivo=resp.model_version or modelo,
        id_resposta=resp.response_id or "",
        tokens_entrada=u.prompt_token_count if u else None,
        tokens_saida=u.candidates_token_count if u else None,
        tokens_cache=u.cached_content_token_count if u else None,
        tokens_raciocinio=u.thoughts_token_count if u else None,
        turnos=1,
        total_tokens=u.total_token_count if u else None,
        motivo_parada=motivo,
        latencia_ms=latencia,
        tentativas=tentativas,
        erro="" if sucesso else f"sem resposta ({motivo or 'motivo não informado'})",
        dono_api=config.dono(FORNECEDOR),
        temperatura_usada=temperatura if temperatura is not None else "não suportada",
    )


def listar_modelos() -> list[tuple[str, str]]:
    modelos = []
    for m in _cliente().models.list():
        if m.supported_actions and "generateContent" not in m.supported_actions:
            continue
        modelos.append(((m.name or "").removeprefix("models/"), m.display_name or ""))
    return sorted(modelos)
