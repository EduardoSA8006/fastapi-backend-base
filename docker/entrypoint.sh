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

echo "Banco disponível. Aplicando migrações..."
alembic upgrade head

echo "Iniciando a aplicação..."
exec "$@"
