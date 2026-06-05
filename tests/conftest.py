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

# O pytest é o DONO dos handlers do root durante a suíte (caplog injeta os
# seus). configure_logging agora substitui handlers pré-existentes do root
# (correção do no-op sob gunicorn) — se rodasse aqui, removeria os handlers
# do pytest no primeiro create_app e quebraria o caplog daquele teste.
# Marcar como já-configurado preserva o comportamento efetivo anterior
# (sob pytest o basicConfig sempre foi no-op); o wiring real é coberto
# explicitamente por test_configure_logging_substitui_handlers_pre_existentes
# (que reseta a flag e restaura o estado do root).
import app.core.logging as _app_logging

_app_logging._configured = True
