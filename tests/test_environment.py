"""ENVIRONMENT: variável-mestra fail-closed no valor E no nome."""

import pytest

from app.core.config import Settings


def test_environment_e_obrigatorio_sem_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail-closed também no NOME da variável: um typo (ENVIRONMNET=production)
    # significa chave ausente — o boot deve falhar com ValidationError, não
    # cair silenciosamente em development (que desligaria todos os guards).
    # _env_file=None isola de um .env local; delenv simula o typo.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(Exception, match="environment"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "bad",
    [
        "prod",
        "prd",
        "produção",
        "dev",
        "",
        "prod\u200b",  # zero-width space: strip() NÃO remove → continua inválido
    ],
)
def test_environment_rejects_unknown_values(bad: str) -> None:
    # Typos plausíveis em deploy não podem virar "modo dev silencioso": o
    # Settings deve falhar na construção em vez de cair no fallback inseguro.
    with pytest.raises(ValueError, match="ENVIRONMENT inválido"):
        Settings(environment=bad)


@pytest.mark.parametrize(
    ("raw", "expected_prod"),
    [
        ("production", True),
        ("  PRODUCTION  ", True),
        ("Production", True),
        ("development", False),
        ("staging", False),
    ],
)
def test_environment_normalized_and_is_production(
    raw: str, expected_prod: bool
) -> None:
    settings = Settings(environment=raw)
    assert settings.environment == raw.strip().lower()
    assert settings.is_production is expected_prod


def test_config_nao_tem_global_de_settings_no_import() -> None:
    # Footgun removido (mesmo guard do database): nada de `settings` global
    # instanciado no import — a app usa app.state.settings, o worker chama
    # get_settings(), e importar o módulo não exige ENVIRONMENT.
    import app.core.config as config_mod

    assert not hasattr(config_mod, "settings")
