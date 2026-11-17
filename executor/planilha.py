"""Leitura e gravação da planilha local versionada (PromptExecucoesSumario_vNNNNN.xlsx)."""

import re
import shutil
import uuid
from datetime import date, datetime, time
from pathlib import Path

from openpyxl import Workbook, load_workbook

from executor import config

ABA_PROMPTS = "Prompts"
ABA_EXECUCOES = "Execuções"
ABA_SUMARIO = "Sumário"
ABA_MODELOS = "Modelos"

STATUS_PRONTO = "Pronto para executar"
STATUS_EXECUTADO = "Executado"

COLUNAS_EXECUCOES = [
    # as cinco primeiras são as da planilha original
    "Identificador Único",
    "Texto do Resultado",
    "Modelo utilizado",
    "Data da Execução",
    "Hora da Execução",
    # colunas de auditoria
    "ID da execução",
    "Fornecedor",
    "Modelo solicitado",
    "ID da resposta",
    "Latência (ms)",
    "Tokens de entrada",
    "Tokens de saída",
    "Tokens de cache",
    "Total de tokens",
    "Motivo de parada",
    "Temperatura",
    "Máximo de tokens de saída",
    "Status",
    "Mensagem de erro",
    "Nº de tentativas",
    "Dono da API",
    "Final da chave",
    "System prompt",
    "Tokens de raciocínio",
    "Nº de turnos",
    "ID do lote",
    "Nº da repetição",
    "Total de repetições",
]

COLUNAS_MODELOS = ["Fornecedor", "Modelos", "Nome de exibição", "Data da consulta"]



# ---------------------------------------------------------------- versões

def _padrao():
    return re.compile(rf"^{re.escape(config.PREFIXO_PLANILHA)}_v(\d+)\.xlsx$")


def versoes(pasta: Path | None = None) -> list[tuple[int, Path]]:
    pasta = pasta or config.PASTA_PLANILHAS
    padrao = _padrao()
    achados = []
    for arquivo in pasta.glob("*.xlsx"):
        m = padrao.match(arquivo.name)
        if m:
            achados.append((int(m.group(1)), arquivo))
    return sorted(achados)


def ultima_versao(pasta: Path | None = None) -> tuple[int, Path]:
    todas = versoes(pasta)
    if not todas:
        pasta = pasta or config.PASTA_PLANILHAS
        raise FileNotFoundError(f"Nenhuma planilha {config.PREFIXO_PLANILHA}_vNNNNN.xlsx em {pasta}")
    return todas[-1]


def nova_versao(pasta: Path | None = None) -> tuple[int, Path, Path]:
    """Copia a última versão para a seguinte. Devolve (número, origem, destino)."""
    numero, origem = ultima_versao(pasta)
    destino = origem.with_name(f"{config.PREFIXO_PLANILHA}_v{numero + 1:05d}.xlsx")
    if destino.exists():
        raise FileExistsError(destino)
    shutil.copy2(origem, destino)
    return numero + 1, origem, destino


# ---------------------------------------------------------------- utilidades

def _aba(wb: Workbook, nome: str):
    if nome in wb.sheetnames:
        return wb[nome]
    return wb.create_sheet(nome)


def _ultima_linha_preenchida(ws) -> int:
    """O export do Google traz ~1000 linhas vazias formatadas; ws.max_row não serve."""
    for linha in range(ws.max_row, 0, -1):
        if any(c.value not in (None, "") for c in ws[linha]):
            return linha
    return 0


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


# ---------------------------------------------------------------- Prompts

def ler_prompts_prontos(wb: Workbook) -> list[dict]:
    """Linhas da aba Prompts com Status = 'Pronto para executar'.

    Colunas: A=ID, B=Prompt, C=Status, D=Fornecedor (opcional), E=Modelo (opcional),
    F=System prompt (opcional; vale só para a linha).
    """
    ws = wb[ABA_PROMPTS]
    prontos = []
    for linha in range(2, _ultima_linha_preenchida(ws) + 1):
        valores = [ws.cell(linha, c).value for c in range(1, 7)]
        id_, prompt, status, fornecedor, modelo, system_prompt = valores
        if _texto(prompt) and _texto(status).lower() == STATUS_PRONTO.lower():
            prontos.append({
                "linha": linha,
                "id": _texto(id_),
                "prompt": str(prompt),
                "fornecedor": _texto(fornecedor),
                "modelo": _texto(modelo),
                "system_prompt": _texto(system_prompt),
            })
    return prontos


def preparar_prompts(wb: Workbook) -> None:
    """Cria o cabeçalho da coluna F (System prompt) na aba Prompts, se ainda não existir."""
    ws = wb[ABA_PROMPTS]
    if not _texto(ws.cell(1, 6).value):
        ws.cell(1, 6, "System prompt")


def marcar_status(wb: Workbook, linha: int, status: str) -> None:
    wb[ABA_PROMPTS].cell(linha, 3, status)


# ---------------------------------------------------------------- Execuções

def _inferir_fornecedor(modelo: str) -> str:
    m = modelo.lower()
    if m.startswith("gemini"):
        return "Gemini"
    if m.startswith("claude"):
        return "Claude"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "OpenAI"
    return ""


def preparar_execucoes(wb: Workbook) -> None:
    """Garante o cabeçalho completo, preservando as linhas já existentes.

    As cinco primeiras colunas são as da planilha original, então linhas antigas
    ficam no mesmo lugar; as colunas de auditoria que faltam são preenchidas com o que é conhecido.
    """
    ws = _aba(wb, ABA_EXECUCOES)
    largura = max(ws.max_column, len(COLUNAS_EXECUCOES))
    cabecalho = [_texto(ws.cell(1, c).value) for c in range(1, largura + 1)]
    if cabecalho[:len(COLUNAS_EXECUCOES)] == COLUNAS_EXECUCOES:
        return

    # lê as linhas existentes pelo nome da coluna (sem diferenciar maiúsculas)
    nomes = {n.lower(): n for n in COLUNAS_EXECUCOES}
    mapa = {i: nomes[c.lower()] for i, c in enumerate(cabecalho, start=1) if c.lower() in nomes}
    existentes = []
    for linha in range(2, _ultima_linha_preenchida(ws) + 1):
        existentes.append({nome: ws.cell(linha, i).value for i, nome in mapa.items()})

    if ws.max_row:
        ws.delete_rows(1, ws.max_row)
    ws.append(COLUNAS_EXECUCOES)

    for registro in existentes:
        data = registro.get("Data da Execução")
        if isinstance(data, (datetime, date)):
            registro["Data da Execução"] = data.strftime("%d/%m/%Y")
        hora = registro.get("Hora da Execução")
        if isinstance(hora, (datetime, time)):
            registro["Hora da Execução"] = hora.strftime("%H:%M:%S")
        modelo = _texto(registro.get("Modelo utilizado"))
        padrao = {
            "ID da execução": f"migrado-{uuid.uuid4()}",
            "Fornecedor": _inferir_fornecedor(modelo),
            "Modelo solicitado": modelo,
            "Status": "sucesso",
            "Mensagem de erro": "registro migrado do layout antigo (campos de auditoria não disponíveis)",
        }
        # só completa linhas do layout original (sem ID de execução); execuções reais ficam como estão
        if registro.get("ID da execução") in (None, ""):
            for nome, valor in padrao.items():
                if registro.get(nome) in (None, ""):
                    registro[nome] = valor
        ws.append([registro.get(c) for c in COLUNAS_EXECUCOES])


def acrescentar_execucao(wb: Workbook, registro: dict) -> None:
    """Sempre acrescenta: nunca sobrescreve execuções anteriores."""
    ws = wb[ABA_EXECUCOES]
    linha = _ultima_linha_preenchida(ws) + 1
    for coluna, nome in enumerate(COLUNAS_EXECUCOES, start=1):
        ws.cell(linha, coluna, registro.get(nome))


def ler_execucoes(wb: Workbook) -> list[dict]:
    ws = wb[ABA_EXECUCOES]
    registros = []
    for linha in range(2, _ultima_linha_preenchida(ws) + 1):
        valores = [ws.cell(linha, c).value for c in range(1, len(COLUNAS_EXECUCOES) + 1)]
        registros.append(dict(zip(COLUNAS_EXECUCOES, valores)))
    return registros


# ---------------------------------------------------------------- Modelos

def ler_catalogo(wb: Workbook) -> dict[str, set[str]]:
    """{nome do fornecedor em minúsculas: {ids de modelo}} a partir da aba Modelos."""
    if ABA_MODELOS not in wb.sheetnames:
        return {}
    ws = wb[ABA_MODELOS]
    catalogo: dict[str, set[str]] = {}
    for linha in range(2, _ultima_linha_preenchida(ws) + 1):
        fornecedor = _texto(ws.cell(linha, 1).value).lower()
        modelo = _texto(ws.cell(linha, 2).value)
        if fornecedor and modelo:
            catalogo.setdefault(fornecedor, set()).add(modelo)
    return catalogo


def gravar_catalogo(wb: Workbook, fornecedor_nome: str, modelos: list[tuple[str, str]]) -> None:
    """Substitui a lista do fornecedor na aba Modelos, mantendo a dos demais."""
    ws = _aba(wb, ABA_MODELOS)
    outros = []
    for linha in range(2, _ultima_linha_preenchida(ws) + 1):
        valores = [ws.cell(linha, c).value for c in range(1, len(COLUNAS_MODELOS) + 1)]
        if _texto(valores[0]).lower() != fornecedor_nome.lower():
            outros.append(valores)

    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    if ws.max_row:
        ws.delete_rows(1, ws.max_row)
    ws.append(COLUNAS_MODELOS)
    for valores in outros:
        ws.append(valores)
    for id_modelo, nome in modelos:
        ws.append([fornecedor_nome, id_modelo, nome, agora])
