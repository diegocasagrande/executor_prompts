"""Registro dos fornecedores. Todos expõem executar(modelo, prompt, parametros) e listar_modelos()."""

from executor.provedores import claude, gemini, openai_

PROVEDORES = {"gemini": gemini, "claude": claude, "openai": openai_}
