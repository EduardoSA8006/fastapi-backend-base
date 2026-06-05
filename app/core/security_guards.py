"""Política compartilhada de credenciais fracas e guards fail-closed.

Vive em um módulo próprio (não em main.py) porque tem DOIS consumidores que
não podem se importar mutuamente: a API (app.main.create_app) e o worker do
Celery (app.worker) — o worker não chama create_app, então os guards de
produção precisam ser invocáveis de ambos os processos.
"""

from urllib.parse import urlparse

from app.core.config import Settings

# Senhas notoriamente fracas/default que não podem ir para produção.
# "classup-minio-dev" é o default de dev do MinIO (que exige >= 8 chars) —
# por ser público no repositório, é tão fraco quanto "classup".
WEAK_PASSWORDS = {
    "classup",
    "classup-minio-dev",
    "postgres",
    "password",
    "changeme",
    "admin",
    "",
}

# Usuários admin previsíveis do MinIO. Diferente do banco (cujo usuário não é
# segredo), o root do MinIO é a credencial de admin do storage; um nome óbvio
# facilita enumeração caso a porta vaze. Defesa-em-profundidade sobre o
# isolamento de rede.
WEAK_MINIO_USERS = {"classup", "minio", "admin", "root", "minioadmin", ""}

# Schemes aceitos para broker/result-backend do Celery. A arquitetura usa um
# Redis dedicado (redis-celery); rediss:// (TLS) fica aceito desde já para o
# caso de o broker um dia cruzar a fronteira de host.
_CELERY_ALLOWED_SCHEMES = {"redis", "rediss"}


# Porta default do Redis — normaliza URLs sem porta explícita para que
# redis://host/0 e redis://host:6379/0 contem como a MESMA instância.
_REDIS_DEFAULT_PORT = 6379


def _host_port(url: str) -> tuple[str | None, int]:
    parsed = urlparse(url)
    return parsed.hostname, parsed.port or _REDIS_DEFAULT_PORT


def validate_celery_security(settings: Settings) -> None:
    """Guards fail-closed do Celery — só têm efeito em produção.

    Chamado tanto pelo create_app (API) quanto no import de app.worker
    (worker/beat): qualquer processo que toque o broker valida a config
    antes de subir. Fora de produção não levanta (dev usa defaults fracos).
    """
    if not settings.is_production:
        return

    for label, url in (
        ("broker (CELERY_BROKER_URL)", settings.celery_broker_url),
        ("result backend (CELERY_RESULT_BACKEND)", settings.celery_result_backend),
    ):
        parsed = urlparse(url)
        # Só o transporte Redis da arquitetura — recusa memory:// (perde
        # mensagens), amqp:// etc., que não passariam pelos guards abaixo.
        if parsed.scheme not in _CELERY_ALLOWED_SCHEMES:
            raise ValueError(
                f"Celery em produção exige redis:// ou rediss:// no {label}; "
                f"recebido scheme {parsed.scheme!r}."
            )
        # Mesma régua de senha do banco/Redis/MinIO; ausente vira "" (fraca).
        if (parsed.password or "") in WEAK_PASSWORDS:
            raise ValueError(
                f"Senha do Celery default/fraca (ou ausente) no {label} não é "
                "permitida em produção. Use uma senha forte "
                "(redis://:SENHA@redis-celery:6379/N)."
            )

    # Invariante arquitetural: broker/backend NÃO podem ser a mesma instância
    # (host:port) do Redis do rate-limit. A separação garante que um task
    # comprometido não alcança as chaves do rate-limit (e vice-versa) e que
    # um FLUSH/saturação de um lado não derruba o outro.
    if settings.rate_limit_enabled and settings.rate_limit_storage_uri.startswith(
        "redis"
    ):
        rl_instance = _host_port(settings.rate_limit_storage_uri)
        for label, url in (
            ("broker", settings.celery_broker_url),
            ("result backend", settings.celery_result_backend),
        ):
            if _host_port(url) == rl_instance:
                raise ValueError(
                    f"O {label} do Celery aponta para a mesma instância Redis "
                    "do rate-limit (host:porta iguais). Em produção use uma "
                    "instância dedicada (ex.: redis-celery) — a separação "
                    "contém o raio de explosão de um task comprometido."
                )
