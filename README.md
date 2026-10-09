# mini-redis-grpc

In-memory key-value хранилище (mini-Redis) поверх gRPC: TTL, LRU eviction на 10 ключей, потокобезопасная обработка параллельных запросов. Контракт — `kvstore.proto` (не изменяется).

## Структура
 | Файл | Назначение |
 |---|---|
 | `kvstore.proto` | контракт сервиса (менять нельзя) |
 | `kvstore_pb2.py` / `.pyi` | сгенерированный protoc-код + type-stubs |
 | `kvstore_pb2_grpc.py` | сгенерированный gRPC-код |
 | `src/store.py` | хранилище: Lock + OrderedDict, ленивая экспирация, LRU |
 | `src/server.py` | gRPC-сервис и запуск сервера на порту 8000 |
 | `tests/test_kvstore.py` | интеграционные тесты (TTL, LRU, конкурентность) |
 | `Dockerfile` / `run.sh` | образ сервиса и точка входа контейнера |
 | `docker-compose.yml` | локальный запуск контейнера |
 | `.github/workflows/ci.yml` | CI/CD: линтер, тесты, сборка образа, деплой в LMS |

## Генерация кода из .proto

```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. --pyi_out=. kvstore.proto
```

## Установка и запуск

```bash
uv sync                       # зависимости (grpcio, grpcio-tools; dev: pytest, ruff, mypy)
uv run python -m src.server   # сервер слушает [::]:8000
```

## Проверки

```bash
uv run pytest -q              # тесты
uv run ruff check .           # линтер
uv run mypy .                 # типы
```

## Поведение

- **`Put(key, value, ttl_seconds)`** — добавить/обновить запись; `ttl_seconds = 0` — без TTL; пустой ключ → `INVALID_ARGUMENT`.
- **`Get(key)`** — значение или `NOT_FOUND` (ключ отсутствует либо TTL истёк). Обновляет порядок LRU.
- **`Delete(key)`** — удалить запись; отсутствующий ключ → `NOT_FOUND`.
- **`List(prefix)`** — все неистёкшие пары с указанным префиксом (пустой префикс — все ключи).
- При превышении 10 ключей вытесняется наименее недавно использованный (использованием считаются `Put` и `Get`).

## Docker

Сборка образа (Python 3.13 + uv, запуск через `run.sh`, порт 8000):

```bash
docker build -t mini-redis-grpc .
```

Запуск контейнера:

```bash
docker run -p 8000:8000 mini-redis-grpc
# или через compose:
docker compose up --build -d
```

Хранилище in-memory — внешние базы/том не требуются, поэтому в `docker-compose.yml`
один сервис `app` с пробросом порта `8000:8000`.

## CI/CD

Пайплайн (`.github/workflows/ci.yml`) запускается на `push`/`pull_request` в `main`
и состоит из трёх jobs:

1. **Lint and Test** — `uv sync --all-groups`, затем `ruff check`, `ruff format --check`
   и `pytest tests/ -v`. Выполняется всегда, включая PR.
2. **Build Docker Image** — только push в `main`, после успешного линта: сборка через
   `docker/build-push-action` для платформ `linux/amd64` и `linux/arm64`, публикация
   образа в GHCR под тегом `ghcr.io/<owner>/<repo>:<git-sha>` (логин — встроенный
   `GITHUB_TOKEN`, отдельных секретов не нужен).
3. **Deploy to LMS** — только push в `main`, после сборки: POST-запрос на
   `https://lms.python-labs.ru/api/deploy-requests` с JSON `{"image": "...", "repository": "..."}`
   и заголовком `X-API-Key`; при HTTP-коде, отличном от 200, шаг падает с телом ответа.

### Требования к настройке репозитория

- В workflow заданы права `permissions: packages: write` — без них встроенный
  `GITHUB_TOKEN` не может создать пакет в GHCR (ошибка вида
  `denied: installation not allowed to Create organization package`). Если в
  **Settings → Actions → General → Workflow permissions** выбрано «Read repository
  contents only», переключите на **Read and write permissions**.
- В **Settings → Secrets and variables → Actions** добавить secret `LMS_API_KEY`
  (ключ API LMS для деплоя).

## Пример использования (grpcio-channel)

```python
import grpc
import kvstore_pb2
import kvstore_pb2_grpc

channel = grpc.insecure_channel("localhost:8000")
stub = kvstore_pb2_grpc.KeyValueStoreStub(channel)

stub.Put(kvstore_pb2.PutRequest(key="user:1", value="Alice", ttl_seconds=60))
print(stub.Get(kvstore_pb2.GetRequest(key="user:1")).value)  # Alice
print([kv.key for kv in stub.List(kvstore_pb2.ListRequest(prefix="user:")).items])
```
