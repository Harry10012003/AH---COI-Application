from __future__ import annotations

import pymssql
from dbutils.pooled_db import PooledDB

from backend.engine.sql_sources.read_only import assert_read_only_sql
from backend.engine.sql_sources.metrics import timed_execute


class CursorWrapper:
    def __init__(self, cursor, source_key: str = "main"):
        self._cursor = cursor
        self._source_key = source_key

    def execute(self, operation, params=None):
        assert_read_only_sql(operation)
        if isinstance(operation, str) and "?" in operation:
            operation = operation.replace("?", "%s")
        return timed_execute(
            self._source_key,
            lambda: self._cursor.execute(operation, params),
        )

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class ConnectionWrapper:
    def __init__(self, conn, source_key: str = "main"):
        self._conn = conn
        self._source_key = source_key

    def cursor(self):
        return CursorWrapper(self._conn.cursor(), self._source_key)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def timeout(self):
        return getattr(self._conn, "timeout", 0)

    @timeout.setter
    def timeout(self, value):
        self._conn.timeout = value

    def __getattr__(self, name):
        return getattr(self._conn, name)


def create_pool(
    server: str,
    user: str,
    password: str,
    database: str,
    *,
    maxconnections: int = 20,
    mincached: int = 2,
    maxcached: int = 10,
    timeout: int = 30,
    tds_version: str = "7.0",
) -> PooledDB:
    host = server
    port = 1433
    if ":" in host:
        host, port_str = host.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            pass
    return PooledDB(
        creator=pymssql,
        maxconnections=maxconnections,
        mincached=mincached,
        maxcached=maxcached,
        blocking=True,
        server=host,
        port=port,
        user=user,
        password=password,
        database=database,
        timeout=timeout,
        tds_version=tds_version,
    )