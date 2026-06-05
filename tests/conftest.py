"""Configuração global da suíte.

ENVIRONMENT é OBRIGATÓRIO (sem default — fail-closed contra typo no nome da
variável; ver test_environment_e_obrigatorio_sem_fallback). A suíte roda em
development: define aqui, ANTES de qualquer import de app.*, para que os
módulos com Settings de import-time (app.core.config, app.worker) e todo
`Settings()` dos testes resolvam o ambiente sem precisar de um .env local.
O teste do próprio guard usa monkeypatch.delenv para simular a ausência.
"""

import os

os.environ.setdefault("ENVIRONMENT", "development")
