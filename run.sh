#!/usr/bin/env bash
# Точка входа контейнера: запускает gRPC-сервер kvstore на порту 8000.
set -euo pipefail

cd /app
exec .venv/bin/python -m src.server
