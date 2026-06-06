#!/usr/bin/env bash
set -e

# Aguarda o banco de dados ficar disponível antes de prosseguir.
echo "Aguardando o banco de dados em ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}..."
until python -c "
import socket, os, sys
host = os.getenv('POSTGRES_HOST', 'db')
port = int(os.getenv('POSTGRES_PORT', '5432'))
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(2)
try:
    sock.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
"; do
  echo "Banco ainda indisponível — aguardando..."
  sleep 1
done

echo "Banco disponível."

# Migrações no boot: conveniente em dev. Em produção com múltiplas réplicas,
# prefira rodar a migração como etapa separada de deploy (job one-shot) para
# evitar corrida entre containers — defina RUN_MIGRATIONS_ON_START=false.
if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  echo "Aplicando migrações (alembic upgrade head)..."
  alembic upgrade head
else
  echo "RUN_MIGRATIONS_ON_START=false — pulando migrações no boot."
fi

echo "Iniciando a aplicação..."
exec "$@"
