"""Baseline: marco zero do schema (sem objetos).

Revisão deliberadamente vazia — serve de âncora da cadeia de migrações:
o `upgrade head` deixa de ser no-op (cria e grava alembic_version), o que a
integração asserta, validando a máquina de migração completa. As migrações
de domínio encadearão a partir daqui.

Revision ID: 1013941a4121
Revises:
Create Date: 2026-06-05
"""

from collections.abc import Sequence

revision: str = "1013941a4121"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Baseline — nenhum objeto de schema ainda (domínio não implementado)."""


def downgrade() -> None:
    """Baseline — nada a desfazer."""
