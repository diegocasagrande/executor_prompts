"""Fornecedor OpenAI (Responses API)."""

import openai

from executor import config
from executor.provedores.base import Parametros, Resultado, com_retentativas, rejeita_temperatura

FORNECEDOR = "openai"
TRANSITORIOS = {408, 409, 429, 500, 502, 503, 504}


def _cliente():
    return openai.OpenAI(api_key=config.chave(FORNECEDOR), max_retries=0)


def _eh_transitorio(erro):
    if isinstance(erro, openai.APIConnectionError):
        return True
    return isinstance(erro, openai.APIStatusError) and erro.status_code in TRANSITORIOS


def executar(modelo: str, prompt: str, p: Parametros) -> Resultado:
    cliente = _cliente()
    temperatura = p.temperatura
    tentativas = 0

    def chamada():
        args = {"model": modelo, "input": prompt, "max_output_tokens": p.max_tokens}
        if temperatura is not None:
            args["temperature"] = temperatura
        if p.system_prompt:
            args["instructions"] = p.system_prompt
        return cliente.responses.with_raw_response.create(**args)

    try:
        try:
            bruto, tentativas, latencia = com_retentativas(chamada, _eh_transitorio, config.MAX_TENTATIVAS)
        except openai.BadRequestError as erro:
            if temperatura is None or not rejeita_temperatura(erro):
                raise
            tentativas = erro.tentativas
            temperatura = None
            bruto, extra, latencia = com_retentativas(chamada, _eh_transitorio, config.MAX_TENTATIVAS)
            tentativas += extra
    except Exception as erro:  # noqa: BLE001
        return Resultado(
            sucesso=False,
            erro=f"{type(erro).__name__}: {erro}",
            tentativas=getattr(erro, "tentativas", tentativas or 1),
            temperatura_usada=temperatura if temperatura is not None else "não suportada",
            dono_api=config.dono(FORNECEDOR),
        )

    r = bruto.parse()
    u = r.usage
    motivo = r.status or ""
    if r.incomplete_details and r.incomplete_details.reason:
        motivo = f"{motivo}: {r.incomplete_details.reason}"

    org = bruto.headers.get("openai-organization", "")
    projeto = bruto.headers.get("openai-project", "")
    dono = " | ".join(
        x for x in (config.dono(FORNECEDOR), f"org={org}" if org else "", f"projeto={projeto}" if projeto else "") if x
    )

    return Resultado(
        sucesso=r.status == "completed",
        texto=r.output_text or "",
        modelo_efetivo=r.model,
        id_resposta=r.id,
        tokens_entrada=u.input_tokens if u else None,
        tokens_saida=u.output_tokens if u else None,
        tokens_cache=(u.input_tokens_details.cached_tokens if u and u.input_tokens_details else None),
        total_tokens=u.total_tokens if u else None,
        motivo_parada=motivo,
        latencia_ms=latencia,
        tentativas=tentativas,
        erro="" if r.status == "completed" else f"resposta {motivo}",
        dono_api=dono,
        temperatura_usada=temperatura if temperatura is not None else "não suportada",
    )


def listar_modelos() -> list[tuple[str, str]]:
    return sorted((m.id, "") for m in _cliente().models.list())
