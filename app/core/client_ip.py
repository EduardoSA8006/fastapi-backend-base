"""Resolução do IP real do cliente, compartilhada entre rate-limit e logs.

Centraliza a lógica de extrair o IP do cliente respeitando a confiança em proxy,
para que o rate-limit (key_func) e o log de acesso concordem sobre quem é o
cliente — atrás de proxy, divergir atrapalha a investigação de abuso.
"""


def resolve_client_ip(
    forwarded_for: str | None,
    connection_ip: str,
    trust_proxy: bool,
    num_trusted_proxies: int,
) -> str:
    """Retorna o IP do cliente a partir do X-Forwarded-For ou da conexão.

    Quando trust_proxy=True, o IP real é extraído de X-Forwarded-For contando
    `num_trusted_proxies` saltos a partir da direita: as entradas mais à direita
    são as que os proxies confiáveis acrescentaram, então o cliente é a
    `num_trusted_proxies`-ésima de trás para frente. As entradas mais à esquerda
    são controláveis pelo cliente e não devem ser usadas (permitiriam forjar IP).

    Só confia se houver entradas suficientes para os saltos esperados; caso
    contrário, recorre ao IP da conexão (seguro).

    ATENÇÃO: trust_proxy=True só é seguro com `num_trusted_proxies` proxies reais
    reescrevendo X-Forwarded-For à frente. Sem esse proxy, o cliente controla o
    valor e pode forjar o IP.
    """
    if trust_proxy and forwarded_for:
        parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        hops = num_trusted_proxies
        if hops >= 1 and len(parts) >= hops:
            return parts[-hops]
    return connection_ip
