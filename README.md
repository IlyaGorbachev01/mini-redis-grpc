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
