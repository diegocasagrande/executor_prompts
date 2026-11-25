"""Orquestra a execução: escolhe o que rodar, versiona a planilha, registra e sumariza."""

import dataclasses
import threading
import time
import uuid
from datetime import datetime

from openpyxl import load_workbook

from executor import config, planilha, sumario
from executor.log import log
from executor import log as registro_log
from executor.provedores import PROVEDORES
from executor.provedores.base import NOMES, Parametros, Resultado, normaliza_fornecedor

_trava = threading.Lock()  # evita duas execuções criando a mesma versão


class ErroDeEntrada(ValueError):
    pass


def _normalizar_selecao(selecao: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[dict]]:
    """Normaliza nomes de fornecedor e remove os que não têm credencial no .env."""
    validos, ignorados = {}, []
    for nome, modelos in selecao.items():
        fornecedor = normaliza_fornecedor(nome)
        if not fornecedor:
            raise ErroDeEntrada(f"Fornecedor desconhecido: {nome!r}. Use gemini, claude ou openai.")
        if not config.tem_credencial(fornecedor):
            motivo = ("comando claude não encontrado no PATH do servidor (ajuste CLAUDE_CODE_BIN no .env "
                      "ou rode scripts/diagnostico_claude_code.py)") if fornecedor == "claude_code" else "sem credencial no .env"
            ignorados.append({"fornecedor": NOMES[fornecedor], "motivo": motivo})
            continue
        validos.setdefault(fornecedor, []).extend(m.strip() for m in (modelos or []) if m and m.strip())
    return validos, ignorados


def _alvos(prompt: dict, selecao: dict[str, list[str]]) -> tuple[list[tuple[str, str]], str]:
    """Regra: colunas Fornecedor/Modelo da linha definem; a requisição filtra.

    - Fornecedor + Modelo na linha: roda se o fornecedor estiver na requisição e
      (a requisição não listar modelos para ele, ou listar esse modelo).
    - Só Fornecedor na linha: roda nos modelos da requisição para esse fornecedor.
    - Linha sem Fornecedor/Modelo: roda em todos os modelos da requisição.
    """
    fornecedor = normaliza_fornecedor(prompt["fornecedor"])
    modelo = prompt["modelo"]

    # Linha "Claude" e requisição só com a assinatura: roda pelo Claude Code.
    if fornecedor == "claude" and "claude" not in selecao and "claude_code" in selecao:
        fornecedor = "claude_code"

    if prompt["fornecedor"] and not fornecedor:
        return [], f"fornecedor {prompt['fornecedor']!r} desconhecido na aba Prompts"
    if modelo and not fornecedor:
        return [], "linha tem Modelo mas não tem Fornecedor"

    if fornecedor and modelo:
        if fornecedor not in selecao:
            return [], f"{NOMES[fornecedor]} não foi solicitado ou não tem credencial"
        if selecao[fornecedor]:
            # os aliases do Claude Code (haiku, sonnet, opus) não diferenciam maiúsculas
            casados = [m for m in selecao[fornecedor]
                       if m == modelo or (fornecedor == "claude_code" and m.lower() == modelo.lower())]
            if not casados:
                return [], f"modelo {modelo!r} não está entre os solicitados para {NOMES[fornecedor]}"
            modelo = casados[0]
        elif fornecedor == "claude_code":
            modelo = modelo.lower()
        return [(fornecedor, modelo)], ""

    if fornecedor:
        alvos = [(fornecedor, m) for m in selecao.get(fornecedor, [])]
        return alvos, "" if alvos else f"nenhum modelo de {NOMES[fornecedor]} informado na requisição"

    alvos = [(f, m) for f, modelos in selecao.items() for m in modelos]
    return alvos, "" if alvos else "linha sem Fornecedor/Modelo e nenhum modelo informado na requisição"


def _registro(prompt_id, fornecedor, modelo, r: Resultado, p: Parametros,
              lote: str = "", repeticao: int = 1, repeticoes: int = 1) -> dict:
    agora = datetime.now()
    return {
        "Identificador Único": prompt_id,
        "Texto do Resultado": r.texto,
        "Modelo utilizado": r.modelo_efetivo or modelo,
        "Data da Execução": agora.strftime("%d/%m/%Y"),
        "Hora da Execução": agora.strftime("%H:%M:%S"),
        "ID da execução": str(uuid.uuid4()),
        "Fornecedor": NOMES[fornecedor],
        "Modelo solicitado": modelo,
        "ID da resposta": r.id_resposta,
        "Latência (ms)": r.latencia_ms,
        "Tokens de entrada": r.tokens_entrada,
        "Tokens de saída": r.tokens_saida,
        "Tokens de cache": r.tokens_cache,
        "Total de tokens": r.total_tokens,
        "Motivo de parada": r.motivo_parada,
        "Temperatura": r.temperatura_usada,
        "Máximo de tokens de saída": p.max_tokens,
        "Status": "sucesso" if r.sucesso else "erro",
        "Mensagem de erro": r.erro[:2000],
        "Nº de tentativas": r.tentativas,
        "Dono da API": r.dono_api,
        "Final da chave": config.final_da_chave(fornecedor),
        "System prompt": r.system_prompt_usado or p.system_prompt or "",
        "Tokens de raciocínio": r.tokens_raciocinio,
        "Nº de turnos": r.turnos,
        "ID do lote": lote,
        "Nº da repetição": repeticao,
        "Total de repetições": repeticoes,
    }


def _log_resultado(n, total, prompt_id, fornecedor, modelo, r: Resultado, registro: dict) -> None:
    prompt_id = f"{prompt_id} (rep. {registro['Nº da repetição']}/{registro['Total de repetições']})"
    metadados = (
        f"modelo utilizado={registro['Modelo utilizado']} | latência={r.latencia_ms} ms | "
        f"tokens entrada={r.tokens_entrada} saída={r.tokens_saida} raciocínio={r.tokens_raciocinio} "
        f"cache={r.tokens_cache} total={r.total_tokens} | parada={r.motivo_parada or '-'} | "
        f"turnos={r.turnos} | tentativas={r.tentativas} | id resposta={r.id_resposta or '-'}"
    )
    if r.sucesso:
        log.info("[%d/%d] %s | %s | %s | SUCESSO | %s", n, total, prompt_id, NOMES[fornecedor], modelo, metadados)
        registro_log.resposta(r.texto)
    else:
        log.error("[%d/%d] %s | %s | %s | ERRO | %s | %s", n, total, prompt_id, NOMES[fornecedor], modelo,
                  r.erro, metadados)
        if r.texto:
            registro_log.resposta(r.texto)


def executar_lote(selecao: dict[str, list[str]], repeticoes: int = 1, parametros: Parametros | None = None) -> dict:
    p = parametros or Parametros()
    if repeticoes < 1:
        raise ErroDeEntrada("repeticoes deve ser >= 1")

    with _trava:
        inicio_lote = time.perf_counter()
        lote = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        log.info("=== Início do lote %s | seleção=%s | repetições=%d | temperatura=%s | max_tokens=%d",
                 lote, selecao, repeticoes, p.temperatura, p.max_tokens)
        selecao, ignorados = _normalizar_selecao(selecao)

        # 1. planeja a partir da última versão, sem alterá-la
        _, origem = planilha.ultima_versao()
        wb_origem = load_workbook(origem)
        prontos = planilha.ler_prompts_prontos(wb_origem)
        wb_origem.close()

        plano = []
        for prompt in prontos:
            alvos, motivo = _alvos(prompt, selecao)
            if alvos:
                plano.append((prompt, alvos))
            else:
                ignorados.append({"prompt": prompt["id"], "motivo": motivo})

        total = sum(len(alvos) for _, alvos in plano) * repeticoes
        log.info("Planilha de origem: %s | prompts prontos: %d | a executar: %d prompts, %d chamadas",
                 origem.name, len(prontos), len(plano), total)
        for item in ignorados:
            log.warning("Ignorado: %s", item)

        if not plano:
            log.info("=== Nada a executar; nenhuma nova versão foi criada.")
            return {
                "planilha_origem": origem.name,
                "planilha_gerada": None,
                "mensagem": "Nada a executar; nenhuma nova versão foi criada.",
                "execucoes": [],
                "ignorados": ignorados,
            }

        # 2. cria a nova versão e grava nela
        numero, _, destino = planilha.nova_versao()
        wb = load_workbook(destino)
        planilha.preparar_execucoes(wb)
        planilha.preparar_prompts(wb)
        catalogo = planilha.ler_catalogo(wb)
        wb.save(destino)
        log.info("Nova versão: %s", destino.name)

        execucoes = []
        n = 0
        for prompt, alvos in plano:
            algum_sucesso = False
            # o system prompt vem só da coluna F da aba Prompts (por linha)
            p_linha = dataclasses.replace(p, system_prompt=prompt["system_prompt"] or None)
            for fornecedor, modelo in alvos:
                conhecidos = catalogo.get(NOMES[fornecedor].lower())
                for rep in range(1, repeticoes + 1):
                    n += 1
                    log.info("[%d/%d] %s | %s | %s | repetição %d/%d | início",
                             n, total, prompt["id"], NOMES[fornecedor], modelo, rep, repeticoes)
                    if conhecidos and modelo not in conhecidos:
                        r = Resultado(
                            erro=f"modelo {modelo!r} não consta na aba Modelos para {NOMES[fornecedor]}; "
                                 "use o ID exato ou atualize o catálogo",
                            dono_api=config.dono(fornecedor),
                            temperatura_usada=p.temperatura,
                        )
                    else:
                        r = PROVEDORES[fornecedor].executar(modelo, prompt["prompt"], p_linha)
                    registro = _registro(prompt["id"], fornecedor, modelo, r, p_linha, lote, rep, repeticoes)
                    _log_resultado(n, total, prompt["id"], fornecedor, modelo, r, registro)
                    planilha.acrescentar_execucao(wb, registro)
                    wb.save(destino)  # salva a cada chamada para não perder nada
                    algum_sucesso |= r.sucesso
                    execucoes.append({
                        "prompt": prompt["id"],
                        "fornecedor": NOMES[fornecedor],
                        "modelo": modelo,
                        "repeticao": f"{rep}/{repeticoes}",
                        "status": registro["Status"],
                        "tokens": r.total_tokens,
                        "latencia_ms": r.latencia_ms,
                        "erro": r.erro or None,
                    })
            if algum_sucesso:
                planilha.marcar_status(wb, prompt["linha"], planilha.STATUS_EXECUTADO)

        dados = sumario.atualizar(wb)
        wb.save(destino)
        wb.close()

        sucessos = sum(1 for e in execucoes if e["status"] == "sucesso")
        log.info("=== Fim do lote | %s | %d chamadas | %d sucesso | %d erro | %.1f s",
                 destino.name, len(execucoes), sucessos, len(execucoes) - sucessos, time.perf_counter() - inicio_lote)
        for fornecedor_nome, v in sorted(dados["por_fornecedor"].items()):
            log.info("    Sumário %s: %d sucesso, %d erro (acumulado da planilha)", fornecedor_nome, v["sucesso"], v["erro"])

    return {
        "planilha_origem": origem.name,
        "planilha_gerada": destino.name,
        "versao": numero,
        "lote": lote,
        "execucoes": execucoes,
        "ignorados": ignorados,
        "sumario_por_fornecedor": dados["por_fornecedor"],
    }


def atualizar_modelos(nome_fornecedor: str) -> dict:
    fornecedor = normaliza_fornecedor(nome_fornecedor)
    if not fornecedor:
        raise ErroDeEntrada(f"Fornecedor desconhecido: {nome_fornecedor!r}. Use gemini, claude ou openai.")
    if not config.tem_credencial(fornecedor):
        raise ErroDeEntrada(f"Sem credencial de {NOMES[fornecedor]} no .env")

    log.info("Consultando modelos de %s", NOMES[fornecedor])
    modelos = PROVEDORES[fornecedor].listar_modelos()
    with _trava:
        _, arquivo = planilha.ultima_versao()
        wb = load_workbook(arquivo)
        planilha.gravar_catalogo(wb, NOMES[fornecedor], modelos)
        wb.save(arquivo)
        wb.close()

    log.info("Catálogo de %s: %d modelos gravados na aba Modelos de %s", NOMES[fornecedor], len(modelos), arquivo.name)
    return {
        "fornecedor": NOMES[fornecedor],
        "planilha": arquivo.name,
        "quantidade": len(modelos),
        "modelos": [{"id": i, "nome": n} for i, n in modelos],
    }
