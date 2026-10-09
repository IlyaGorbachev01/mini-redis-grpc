"""Интеграционные тесты: настоящий gRPC-сервер на порту 8000 + параллельные клиенты."""

from __future__ import annotations

import threading
import time
from concurrent import futures
from typing import TYPE_CHECKING

import grpc
import pytest

import kvstore_pb2
import kvstore_pb2_grpc
from src.server import KeyValueStoreServicer

if TYPE_CHECKING:
    from collections.abc import Iterator

TEST_PORT = 8000


@pytest.fixture(scope="module")
def channel() -> Iterator[grpc.Channel]:
    """Поднимает сервер один раз на модуль и отдаёт открытым канал."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    kvstore_pb2_grpc.add_KeyValueStoreServicer_to_server(
        KeyValueStoreServicer(), server
    )
    port = server.add_insecure_port(f"[::]:{TEST_PORT}")
    assert port == TEST_PORT, "сервер должен слушать порт 8000"
    server.start()
    with grpc.insecure_channel(f"localhost:{TEST_PORT}") as chan:
        grpc.channel_ready_future(chan).result(timeout=5)
        yield chan
    server.stop(grace=0)


def test_put_get_delete(channel: grpc.Channel) -> None:
    stub = kvstore_pb2_grpc.KeyValueStoreStub(channel)
    stub.Put(kvstore_pb2.PutRequest(key="a", value="1"))
    assert stub.Get(kvstore_pb2.GetRequest(key="a")).value == "1"
    # обновление
    stub.Put(kvstore_pb2.PutRequest(key="a", value="2"))
    assert stub.Get(kvstore_pb2.GetRequest(key="a")).value == "2"
    stub.Delete(kvstore_pb2.DeleteRequest(key="a"))
    with pytest.raises(grpc.RpcError) as exc:
        stub.Get(kvstore_pb2.GetRequest(key="a"))
    assert exc.value.code() == grpc.StatusCode.NOT_FOUND


def test_ttl_expiry(channel: grpc.Channel) -> None:
    stub = kvstore_pb2_grpc.KeyValueStoreStub(channel)
    stub.Put(kvstore_pb2.PutRequest(key="ttl", value="v", ttl_seconds=1))
    assert stub.Get(kvstore_pb2.GetRequest(key="ttl")).value == "v"
    time.sleep(1.2)
    with pytest.raises(grpc.RpcError) as exc:
        stub.Get(kvstore_pb2.GetRequest(key="ttl"))
    assert exc.value.code() == grpc.StatusCode.NOT_FOUND
    # истёкший ключ не должен появляться и в List
    items = stub.List(kvstore_pb2.ListRequest(prefix="ttl")).items
    assert items == []


def test_list_prefix(channel: grpc.Channel) -> None:
    stub = kvstore_pb2_grpc.KeyValueStoreStub(channel)
    stub.Put(kvstore_pb2.PutRequest(key="user:1", value="alice"))
    stub.Put(kvstore_pb2.PutRequest(key="user:2", value="bob"))
    stub.Put(kvstore_pb2.PutRequest(key="other", value="x"))
    got = {
        kv.key: kv.value
        for kv in stub.List(kvstore_pb2.ListRequest(prefix="user:")).items
    }
    assert got == {"user:1": "alice", "user:2": "bob"}
    all_items = stub.List(kvstore_pb2.ListRequest(prefix=""))
    assert len(all_items.items) <= 10


def test_lru_eviction_max_10(channel: grpc.Channel) -> None:
    stub = kvstore_pb2_grpc.KeyValueStoreStub(channel)
    for i in range(10):
        stub.Put(kvstore_pb2.PutRequest(key=f"k{i}", value=str(i)))
    # «трогаем» k0, чтобы он стал наиболее свежим; теперь LRU — это k1
    stub.Get(kvstore_pb2.GetRequest(key="k0"))
    # вставка ещё одного ключа вытесняет k1 (наименее недавно использованный)
    stub.Put(kvstore_pb2.PutRequest(key="new", value="n"))
    with pytest.raises(grpc.RpcError) as exc:
        stub.Get(kvstore_pb2.GetRequest(key="k1"))
    assert exc.value.code() == grpc.StatusCode.NOT_FOUND
    assert stub.Get(kvstore_pb2.GetRequest(key="k0")).value == "0"
    assert stub.Get(kvstore_pb2.GetRequest(key="new")).value == "n"
    # оставшиеся ключи с префиксом «k»: k0, k2..k9 = 9 ключей
    assert len(stub.List(kvstore_pb2.ListRequest(prefix="k")).items) == 9


def test_delete_missing_returns_not_found(channel: grpc.Channel) -> None:
    stub = kvstore_pb2_grpc.KeyValueStoreStub(channel)
    with pytest.raises(grpc.RpcError) as exc:
        stub.Delete(kvstore_pb2.DeleteRequest(key="nope"))
    assert exc.value.code() == grpc.StatusCode.NOT_FOUND


def test_concurrent_requests(channel: grpc.Channel) -> None:
    """Много потоков одновременно делают Put/Get/Delete без ошибок."""
    stub = kvstore_pb2_grpc.KeyValueStoreStub(channel)
    errors: list[Exception] = []
    barrier = threading.Barrier(32)

    def worker(n: int) -> None:
        barrier.wait()
        try:
            for j in range(50):
                key = f"c{n}:{j}"
                stub.Put(kvstore_pb2.PutRequest(key=key, value=f"{n}-{j}"))
                try:
                    stub.Get(kvstore_pb2.GetRequest(key=key))
                except grpc.RpcError as e:
                    # NOT_FOUND допустим из-за вытеснения по LRU
                    if e.code() != grpc.StatusCode.NOT_FOUND:
                        raise
            try:
                stub.Delete(kvstore_pb2.DeleteRequest(key=f"c{n}:0"))
            except grpc.RpcError as e:
                if e.code() != grpc.StatusCode.NOT_FOUND:
                    raise
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    # хранилище по-прежнему соблюдает инвариант вместимости
    assert len(stub.List(kvstore_pb2.ListRequest(prefix="")).items) <= 10
