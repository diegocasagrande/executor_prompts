import os
import time

from google import genai
from google.genai.errors import ServerError

MODELO = "gemini-3.5-flash-lite"


def executa_prompt(prompt):

    api_key = ""

    client = genai.Client(api_key=api_key)

    chat = client.chats.create(
        model=MODELO,
        history=[],
    )

    mensagem = (
        prompt
    )


    max_tentativas = 5
    for tentativa in range(1, max_tentativas + 1):
        try:
            print("Enviando mensagem para o Gemini...")
            resposta = chat.send_message(message=mensagem)
            break
        except ServerError as erro:
            if getattr(erro, "code", None) != 503:
                raise

            if tentativa == max_tentativas:
                raise

            print(
                f"Servidor indisponível (503). Nova tentativa em 500 ms "
                f"({tentativa}/{max_tentativas - 1})..."
        )
            time.sleep(0.5)

    
    return resposta.text

#resposta = executa_prompt("Me responda somente o nome da melhor universidade do mundo?")
#print(resposta)