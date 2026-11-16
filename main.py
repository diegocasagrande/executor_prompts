import time
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from gemni import MODELO, executa_prompt

PLANILHA = Path(__file__).parent / "planilha" / "Étia em IA.xlsx"
COLUNA_ID = "Identificador do Prompt"

#while True:
print(f"Execução efetuada! {datetime.now():%d/%m/%Y %H:%M:%S}")

wb = load_workbook(PLANILHA)
execucoes = wb["Execuções"]

# A planilha não tem coluna para o ID do prompt; cria na primeira execução
if execucoes.cell(1, 5).value != COLUNA_ID:
    execucoes.cell(1, 5, COLUNA_ID)

for id_, prompt, *_ in wb["Prompts"].iter_rows(min_row=2, values_only=True):
    if prompt:
        print(f"[{id_}] {prompt}")
        resposta = executa_prompt(prompt)
        agora = datetime.now()
        execucoes.append([resposta, MODELO, agora.strftime("%d/%m/%Y"), agora.strftime("%H:%M:%S"), id_])
        wb.save(PLANILHA)

wb.close()

time.sleep(5)
