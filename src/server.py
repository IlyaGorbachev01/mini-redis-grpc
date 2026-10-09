"""gRPC-сервис и точка входа сервера для службы kvstore."""

from __future__ import annotations

import logging
from concurrent import futures

import grpc

import kvstore_pb2
import kvstore_pb2_grpc
from src.store import ExpiredError, Store


class KeyValueStoreServicer(kvstore_pb2_grpc.KeyValueStoreServicer):
    """Реализует Put/Get/Delete/List поверх потокобезопасного Store."""

    # Имена методов повторяют gRPC-контракт (PascalCase), а не PEP8.
    # ruff: noqa: N802

    def __init__(self, store: Store | None = None) -> None:
        self._store = store if store is not None else Store()

    def Put(
        self, request: kvstore_pb2.PutRequest, context: grpc.ServicerContext
    ) -> kvstore_pb2.PutResponse:
        """Добавляет или обновляет ключ; ttl_seconds == 0 означает «без TTL»."""
        if not request.key:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("ключ не может быть пустым")
            return kvstore_pb2.PutResponse()
        self._store.put(request.key, request.value, request.ttl_seconds)
        return kvstore_pb2.PutResponse()

    def Get(
        self, request: kvstore_pb2.GetRequest, context: grpc.ServicerContext
    ) -> kvstore_pb2.GetResponse:
        """Возвращает значение; NOT_FOUND, если ключ отсутствует или истёк."""
        try:
            value = self._store.get(request.key)
        except ExpiredError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"ключ {request.key!r} истёк по TTL")
            return kvstore_pb2.GetResponse()
        except KeyError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"ключ {request.key!r} не найден")
            return kvstore_pb2.GetResponse()
        return kvstore_pb2.GetResponse(value=value)

    def Delete(
        self, request: kvstore_pb2.DeleteRequest, context: grpc.ServicerContext
    ) -> kvstore_pb2.DeleteResponse:
        """Удаляет ключ; NOT_FOUND, если он не существует."""
        try:
            self._store.delete(request.key)
        except KeyError:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"ключ {request.key!r} не найден")
        return kvstore_pb2.DeleteResponse()

    def List(
        self, request: kvstore_pb2.ListRequest, context: grpc.ServicerContext
    ) -> kvstore_pb2.ListResponse:
        """Возвращает все живые (не истёкшие) ключи, начинающиеся с `prefix`."""
        items = [
            kvstore_pb2.KeyValue(key=key, value=value)
            for key, value in self._store.list_by_prefix(request.prefix)
        ]
        return kvstore_pb2.ListResponse(items=items)


PORT = 8000


def serve(port: int = PORT) -> grpc.Server:
    """Запускает gRPC-сервер на пуле потоков по адресу `port` и блокируется.

    Возвращает объект сервера (достижим только после остановки), чтобы
    вызывающий код (например, тесты) мог управлять жизненным циклом через
    `add_to_server`.
    """
    server = build_server(port)
    server.start()
    logging.basicConfig(level=logging.INFO)
    logging.info("KeyValueStore gRPC сервер слушает порт %d", port)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(grace=1)
    return server


def build_server(port: int = PORT, workers: int = 16) -> grpc.Server:
    """Создаёт настроенный gRPC-сервер, но не запускает его."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=workers))
    kvstore_pb2_grpc.add_KeyValueStoreServicer_to_server(
        KeyValueStoreServicer(), server
    )
    server.add_insecure_port(f"[::]:{port}")
    return server


if __name__ == "__main__":
    serve()
