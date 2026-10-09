"""In-memory хранилище «ключ–значение» с TTL и вытеснением по LRU.

Потокобезопасно: все публичные методы защищены одним замком, поэтому
хранилищем можно безопасно делиться между параллельными потоками-обработчиками
gRPC.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Entry:
    """Хранимое значение с необязательной абсолютной меткой истечения срока."""

    value: str
    expires_at: float | None  # абсолютный дедлайн (monotonic); None — TTL нет


class ExpiredError(KeyError):
    """Возникает, когда ключ существует, но его TTL уже истёк."""


class Store:
    """LRU + TTL хранилище «ключ–значение», ограниченное `max_keys` записей."""

    def __init__(self, max_keys: int = 10) -> None:
        self._max_keys = max_keys
        self._entries: OrderedDict[str, Entry] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _deadline(ttl_seconds: int) -> float | None:
        """Преобразует TTL в секундах в абсолютный дедлайн (monotonic)."""
        if ttl_seconds <= 0:
            return None
        return time.monotonic() + ttl_seconds

    @staticmethod
    def _is_expired(entry: Entry) -> bool:
        return entry.expires_at is not None and time.monotonic() >= entry.expires_at

    def put(self, key: str, value: str, ttl_seconds: int = 0) -> None:
        """Добавляет или обновляет `key`; позиция в LRU становится «свежей».

        При превышении вместимости вытесняются наименее недавно
        использованные записи.
        """
        entry = Entry(value=value, expires_at=self._deadline(ttl_seconds))
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_keys:
                self._entries.popitem(last=False)

    def get(self, key: str) -> str:
        """Возвращает значение по `key` и помечает его наиболее свежим.

        Raises:
            ExpiredError: ключ существует, но его TTL истёк (запись
                удаляется лениво).
            KeyError: ключ неизвестен.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise KeyError(key)
            if self._is_expired(entry):
                del self._entries[key]
                raise ExpiredError(key)
            self._entries.move_to_end(key)
            return entry.value

    def delete(self, key: str) -> None:
        """Удаляет `key` из хранилища.

        Raises:
            KeyError: ключ неизвестен.
        """
        with self._lock:
            if key not in self._entries:
                raise KeyError(key)
            del self._entries[key]

    def list_by_prefix(self, prefix: str) -> list[tuple[str, str]]:
        """Возвращает пары (ключ, значение), где ключ начинается с `prefix`.

        Истёкшие ключи пропускаются (и удаляются); порядок вставки/доступа
        сохраняется. Порядок LRU при этом не меняется.
        """
        with self._lock:
            result: list[tuple[str, str]] = []
            expired: list[str] = []
            for key, entry in self._entries.items():
                if self._is_expired(entry):
                    expired.append(key)
                    continue
                if key.startswith(prefix):
                    result.append((key, entry.value))
            for key in expired:
                del self._entries[key]
            return result
