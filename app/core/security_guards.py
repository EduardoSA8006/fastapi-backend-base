"""Guards fail-closed de configuração e política de credenciais fracas.

TODOS os guards de boot vivem aqui (não em main.py): o composition root só
compõe; a política de segurança é deste módulo — testável isoladamente e
invocável pelos DOIS processos que não podem se importar mutuamente: a API
(app.main.create_app) e o worker/scheduler do TaskIQ (app.worker).
"""

import logging
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from app.core.config import Settings

logger = logging.getLogger("myapp")

# Senhas notoriamente fracas/default que não podem ir para produção.
# "myapp-minio-dev" é o default de dev do MinIO (que exige >= 8 chars) —
# por ser público no repositório, é tão fraco quanto "myapp".
WEAK_PASSWORDS = {
    "myapp",
    "myapp-minio-dev",
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
WEAK_MINIO_USERS = {"myapp", "minio", "admin", "root", "minioadmin", ""}


def _reject_if_weak_password(password: str, message: str) -> None:
    """Levanta ValueError(message) se a senha estiver na blocklist de fracas."""
    if password in WEAK_PASSWORDS:
        raise ValueError(message)


# Schemes aceitos para broker/result-backend do TaskIQ. A arquitetura usa um
# Redis dedicado (redis-taskiq); rediss:// (TLS) fica aceito desde já para o
# caso de o broker um dia cruzar a fronteira de host.
_TASKIQ_ALLOWED_SCHEMES = {"redis", "rediss"}


# Porta default do Redis — normaliza URLs sem porta explícita para que
# redis://host/0 e redis://host:6379/0 contem como a MESMA instância.
_REDIS_DEFAULT_PORT = 6379


def _host_port(url: str) -> tuple[str | None, int]:
    parsed = urlparse(url)
    return parsed.hostname, parsed.port or _REDIS_DEFAULT_PORT


def validate_taskiq_security(settings: Settings) -> None:
    """Guards fail-closed do TaskIQ — só têm efeito em produção.

    Chamado pelo create_app (API) e no import de app.worker (worker/scheduler):
    qualquer processo que toque o broker valida a config antes de subir. Fora
    de produção não levanta (dev usa defaults fracos).
    """
    if not settings.is_production:
        return

    for label, url in (
        ("broker (TASKIQ_BROKER_URL)", settings.taskiq_broker_url),
        ("result backend (TASKIQ_RESULT_BACKEND)", settings.taskiq_result_backend),
    ):
        parsed = urlparse(url)
        # Só o transporte Redis da arquitetura — recusa memory:// (perde
        # mensagens), amqp:// etc., que não passariam pelos guards abaixo.
        if parsed.scheme not in _TASKIQ_ALLOWED_SCHEMES:
            raise ValueError(
                f"TaskIQ em produção exige redis:// ou rediss:// no {label}; "
                f"recebido scheme {parsed.scheme!r}."
            )
        # Mesma régua de senha do banco/Redis/MinIO; ausente vira "" (fraca).
        _reject_if_weak_password(
            parsed.password or "",
            f"Senha do TaskIQ default/fraca (ou ausente) no {label} não é "
            "permitida em produção. Use uma senha forte "
            "(redis://:SENHA@redis-taskiq:6379/N).",
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
            ("broker", settings.taskiq_broker_url),
            ("result backend", settings.taskiq_result_backend),
        ):
            if _host_port(url) == rl_instance:
                raise ValueError(
                    f"O {label} do TaskIQ aponta para a mesma instância Redis "
                    "do rate-limit (host:porta iguais). Em produção use uma "
                    "instância dedicada (ex.: redis-taskiq) — a separação "
                    "contém o raio de explosão de um task comprometido."
                )


def validate_universal(settings: Settings) -> None:
    """Guards que valem em QUALQUER ambiente (dev incluso)."""
    # CORS com credenciais + origem curinga reflete origens arbitrárias com
    # credenciais — sempre inseguro.
    if settings.cors_allow_credentials and "*" in settings.cors_allow_origins:
        raise ValueError(
            "cors_allow_credentials=True com cors_allow_origins=['*'] é "
            "inseguro: reflete origens arbitrárias com credenciais."
        )


def validate_production(settings: Settings) -> None:
    """Guards fail-closed de produção. No-op fora de ENVIRONMENT=production.

    Chamado pelo create_app; o subconjunto do TaskIQ também roda no worker
    (via validate_taskiq_security no import de app.worker).
    """
    if not settings.is_production:
        return
    if settings.debug:
        raise ValueError("DEBUG=true não é permitido em produção (vaza stack traces).")
    if "*" in settings.trusted_hosts:
        raise ValueError(
            "trusted_hosts=['*'] em produção desativa a validação de Host. "
            "Defina TRUSTED_HOSTS com os hosts reais."
        )
    if settings.rate_limit_enabled and settings.rate_limit_storage_uri.startswith(
        "memory://"
    ):
        raise ValueError(
            "Rate-limit em produção exige um store compartilhado "
            "(redis://...), não memory://."
        )
    # Paridade entre os guards de credencial: senha default/fraca (ou ausente)
    # é barrada no Redis, no MinIO e no banco. Nenhum deles fica exposto ao
    # host, mas a inconsistência não se justificaria — e protege as chaves/
    # objetos de acesso ou flush por um vizinho de rede comprometido.
    if settings.rate_limit_enabled and settings.rate_limit_storage_uri.startswith(
        "redis"
    ):
        redis_password = urlparse(settings.rate_limit_storage_uri).password or ""
        _reject_if_weak_password(
            redis_password,
            "Senha do Redis default/fraca (ou ausente) não é permitida em "
            "produção. Use uma senha forte na RATE_LIMIT_STORAGE_URI "
            "(redis://:SENHA@host:porta/db).",
        )
    _reject_if_weak_password(
        settings.minio_root_password,
        "Senha do MinIO default/fraca (ou ausente) não é permitida em "
        "produção. Defina MINIO_ROOT_PASSWORD com uma senha forte.",
    )
    # Defesa-em-profundidade: além da senha, o usuário admin do MinIO não pode
    # ser um nome óbvio (root previsível encurta enumeração se a porta vazar).
    if settings.minio_root_user.strip().lower() in WEAK_MINIO_USERS:
        raise ValueError(
            "Usuário do MinIO default/previsível não é permitido em "
            "produção. Defina MINIO_ROOT_USER com um nome não-óbvio."
        )
    # SQLite não tem senha — ignorado.
    if not settings.database_url.startswith("sqlite"):
        db_password = make_url(settings.database_url).password or ""
        _reject_if_weak_password(
            db_password,
            "Senha de banco default/fraca não é permitida em produção. "
            "Use uma senha forte na DATABASE_URL.",
        )
    # Broker/result backend do TaskIQ (compartilhado com o worker).
    validate_taskiq_security(settings)
    # Avisos de topologia de proxy — a app não detecta a topologia com
    # segurança, então não falha o boot; exige confirmação do operador.
    if settings.rate_limit_enabled and not settings.trust_proxy:
        logger.warning(
            "ENVIRONMENT=production com TRUST_PROXY=false: se houver proxy "
            "reverso à frente, o rate-limit colapsa num único bucket (IP do "
            "proxy). Defina TRUST_PROXY=true e NUM_TRUSTED_PROXIES corretamente."
        )
    elif settings.rate_limit_enabled and settings.trust_proxy:
        logger.warning(
            "ENVIRONMENT=production com TRUST_PROXY=true: confiar no "
            "X-Forwarded-For só é seguro atrás de EXATAMENTE "
            f"NUM_TRUSTED_PROXIES={settings.num_trusted_proxies} proxy(ies) "
            "reverso(s) que reescrevem o header. Se a app estiver exposta "
            "diretamente, o cliente forja o IP e burla o rate-limit. Confirme "
            "a topologia de rede."
        )
