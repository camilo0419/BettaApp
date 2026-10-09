"""Bloqueos de ejecución compatibles con MariaDB y SQLite."""

from contextlib import contextmanager
import threading

from django.db import connection


class DatabaseLockUnavailable(RuntimeError):
    """El proceso no pudo adquirir el bloqueo solicitado."""


_sqlite_locks = {}
_sqlite_locks_guard = threading.Lock()


def _sqlite_lock(name):
    with _sqlite_locks_guard:
        return _sqlite_locks.setdefault(name, threading.Lock())


@contextmanager
def advisory_lock(name):
    """Adquiere un bloqueo no bloqueante y lo libera incluso ante excepciones.

    MariaDB usa GET_LOCK en la conexión actual. SQLite usa un bloqueo de
    proceso para las pruebas y ejecuciones locales; la exclusión fuerte de
    procesos independientes queda delegada al motor MariaDB de producción.
    """
    if connection.vendor == "mysql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", [name])
            acquired = cursor.fetchone()[0] == 1
        if not acquired:
            raise DatabaseLockUnavailable("Ya existe otra sincronización entrante en ejecución.")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", [name])
        return

    lock = _sqlite_lock(name)
    if not lock.acquire(blocking=False):
        raise DatabaseLockUnavailable("Ya existe otra sincronización entrante en ejecución.")
    try:
        yield
    finally:
        lock.release()
