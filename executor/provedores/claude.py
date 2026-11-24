"""Fornecedor Claude (Anthropic Messages API)."""

import anthropic

from executor import config
from executor.provedores.base import Parametros, Resultado, com_retentativas

FORNECEDOR = "claude"
TRANSITORIOS = {408, 409, 429, 500, 502, 503, 504, 529}
# Com streaming, um erro que chega no meio do fluxo vem com status HTTP 200 e o tipo no corpo.
TIPOS_TRANSITORIOS = {"overloaded_error", "rate_limit_error", "api_error", "timeout_error"}


def _cliente():
    return anthropic.Anthropic(api_key=config.chave(FORNECEDOR), max_retries=0)


def _eh_transitorio(erro):
    if isinstance(erro, anthropic.APIConnectionError):
        return True
    if not isinstance(erro, anthropic.APIStatusError):
        return False
    if erro.status_code in TRANSITORIOS:
        return True
    corpo = erro.body if isinstance(erro.body, dict) else {}
    tipo = (corpo.get("error") or {}).get("type") if isinstance(corpo.get("error"), dict) else None
    return tipo in TIPOS_TRANSITORIOS


# A Messages API atual não aceita mais temperature/top_p: o parâmetro não é enviado
# e a coluna Temperatura registra "não suportada".
TEMPERATURA = "não suportada"


def executar(modelo: str, prompt: str, p: Parametros) -> Resultado:
    cliente = _cliente()
    tentativas = 0

    def chamada():
        args = {
            "model": modelo,
            "max_tokens": p.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if p.system_prompt:
            args["system"] = p.system_prompt
        # Streaming: o SDK da Anthropic recusa chamadas sem streaming com max_tokens alto
        # (acima de ~21 mil tokens), por poderem passar de 10 minutos. O resultado final
        # é o mesmo de uma chamada comum.
        with cliente.messages.stream(**args) as fluxo:
            mensagem = fluxo.get_final_message()
            headers = fluxo.response.headers
        return mensagem, headers

    try:
        bruto, tentativas, latencia = com_retentativas(chamada, _eh_transitorio, config.MAX_TENTATIVAS)
    except Exception as erro:  # noqa: BLE001
        return Resultado(
            sucesso=False,
            erro=f"{type(erro).__name__}: {erro}",
            tentativas=getattr(erro, "tentativas", tentativas or 1),
            temperatura_usada=TEMPERATURA,
            dono_api=config.dono(FORNECEDOR),
        )

    msg, headers = bruto
    u = msg.usage
    cache_lido = u.cache_read_input_tokens or 0
    cache_gravado = u.cache_creation_input_tokens or 0
    org = headers.get("anthropic-organization-id", "")
    dono = " | ".join(x for x in (config.dono(FORNECEDOR), f"org={org}" if org else "") if x)

    return Resultado(
        sucesso=True,
        texto="".join(b.text for b in msg.content if getattr(b, "type", "") == "text"),
        modelo_efetivo=msg.model,
        id_resposta=msg.id,
        # input_tokens do Claude exclui o cache: soma para ter a entrada total (como Gemini e OpenAI)
        tokens_entrada=u.input_tokens + cache_lido + cache_gravado,
        tokens_saida=u.output_tokens,
        tokens_cache=cache_lido,
        turnos=1,
        # No Claude input_tokens não inclui os tokens de cache: soma tudo.
        total_tokens=u.input_tokens + u.output_tokens + cache_lido + cache_gravado,
        motivo_parada=msg.stop_reason or "",
        latencia_ms=latencia,
        tentativas=tentativas,
        dono_api=dono,
        temperatura_usada=TEMPERATURA,
    )


def listar_modelos() -> list[tuple[str, str]]:
    return [(m.id, m.display_name or "") for m in _cliente().models.list(limit=1000)]
