"""Tests for the collector module (database schema and helpers)."""

import os
import sqlite3
import tempfile

import pytest

from src.collector import init_db, SCHEMA_SQL, _ts_to_iso


class TestInitDb:
    def test_creates_tables(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = init_db(path)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = {t[0] for t in tables}
            assert "metrics" in table_names
            assert "collection_runs" in table_names
            conn.close()
        finally:
            os.unlink(path)

    def test_creates_indexes(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = init_db(path)
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            ).fetchall()
            index_names = {i[0] for i in indexes}
            assert "idx_metrics_name" in index_names
            assert "idx_metrics_timestamp" in index_names
            assert "idx_metrics_name_ts" in index_names
            conn.close()
        finally:
            os.unlink(path)

    def test_idempotent(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn1 = init_db(path)
            conn1.close()
            conn2 = init_db(path)  # Should not raise
            conn2.close()
        finally:
            os.unlink(path)


class TestTimestampConversion:
    def test_unix_to_iso(self):
        result = _ts_to_iso(1710500400.0)
        assert "2024-03-15" in result

    def test_returns_string(self):
        assert isinstance(_ts_to_iso(0), str)
