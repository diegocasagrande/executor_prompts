"""Recalcula a aba Sumário a partir de toda a aba Execuções."""

from collections import defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font

from executor.planilha import ABA_SUMARIO, ler_execucoes


def _numero(valor) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def calcular(execucoes: list[dict]) -> dict:
    por_fornecedor = defaultdict(lambda: {"sucesso": 0, "erro": 0})
    por_modelo = defaultdict(lambda: {"sucesso": 0, "erro": 0, "tokens": 0, "latencias": []})

    for e in execucoes:
        fornecedor = e.get("Fornecedor") or "(não informado)"
        modelo = e.get("Modelo efetivo") or e.get("Modelo solicitado") or "(não informado)"
        resultado = "sucesso" if str(e.get("Status", "")).lower() == "sucesso" else "erro"

        por_fornecedor[fornecedor][resultado] += 1
        m = por_modelo[(fornecedor, modelo)]
        m[resultado] += 1
        m["tokens"] += int(_numero(e.get("Total de tokens")) or 0)
        latencia = _numero(e.get("Latência (ms)"))
        if latencia is not None:
            m["latencias"].append(latencia)

    return {"por_fornecedor": dict(por_fornecedor), "por_modelo": dict(por_modelo)}


def atualizar(wb: Workbook) -> dict:
    dados = calcular(ler_execucoes(wb))

    if ABA_SUMARIO in wb.sheetnames:
        del wb[ABA_SUMARIO]
    ws = wb.create_sheet(ABA_SUMARIO)
    negrito = Font(bold=True)

    ws.append(["Atualizado em", datetime.now().strftime("%d/%m/%Y %H:%M:%S")])
    ws.append([])

    ws.append(["Execuções por fornecedor"])
    ws.cell(ws.max_row, 1).font = negrito
    ws.append(["Fornecedor", "Sucesso", "Erro", "Total"])
    for fornecedor, v in sorted(dados["por_fornecedor"].items()):
        ws.append([fornecedor, v["sucesso"], v["erro"], v["sucesso"] + v["erro"]])
    ws.append([])

    ws.append(["Execuções por modelo"])
    ws.cell(ws.max_row, 1).font = negrito
    ws.append(["Fornecedor", "Modelo", "Execuções", "Sucesso", "Erro", "Total de tokens", "Latência média (ms)"])
    for (fornecedor, modelo), v in sorted(dados["por_modelo"].items()):
        latencias = v["latencias"]
        media = round(sum(latencias) / len(latencias)) if latencias else None
        ws.append([fornecedor, modelo, v["sucesso"] + v["erro"], v["sucesso"], v["erro"], v["tokens"], media])

    for linha in ws.iter_rows():
        for celula in linha:
            if celula.value in ("Fornecedor", "Modelo", "Sucesso", "Erro", "Total", "Execuções",
                                "Total de tokens", "Latência média (ms)", "Atualizado em"):
                celula.font = negrito
    return dados
