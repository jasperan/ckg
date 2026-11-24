"""Oracle AI Database connectivity for CKG.

Reads connection settings from the environment (CKG_ORACLE_*), creates an
oracledb pool, and exposes the small handle the storage layer expects
(``mem._pool``). Oracle is fully optional: when no ``CKG_ORACLE_DSN`` is set,
ckg runs in pure in-memory mode.

Environment variables:
    CKG_ORACLE_DSN              e.g. "localhost:1521/FREEPDB1" (unset → memory mode)
    CKG_ORACLE_USER             default "dmuser"
    CKG_ORACLE_PASSWORD         default "continual_learning"
    CKG_ORACLE_DOMAIN           PGQ domain scope, default "default"
    CKG_ORACLE_GRAPH            property graph name, default "ckg_code_graph"
    CKG_ORACLE_TABLE_PREFIX     node/edge table prefix, default "MEMORY_GRAPH"
    CKG_ORACLE_POOL_MIN/MAX     pool sizing, default 1/4
    CKG_ORACLE_CONNECT_TIMEOUT  seconds to wait for the database, default 3
"""

from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace

from ckg.storage.oracle_pgq import (
    DEFAULT_GRAPH_NAME,
    DEFAULT_TABLE_PREFIX,
    _tables,
    require_identifier,
)

_DEFAULT_PASSWORD = "continual_learning"
_DEFAULT_CONNECT_TIMEOUT = 3.0
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# (dsn, user) → reason, for connections that failed in this process. ckg is
# called once per prompt; a second attempt would only wait out the timeout
# again.
_failed: dict[tuple[str, str], str] = {}
_last_error: str | None = None


def _safe_int(name: str, default: int) -> int:
    """Parse an int env var without crashing on garbage input."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _connect_timeout() -> float:
    try:
        value = float(os.environ.get("CKG_ORACLE_CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT))
    except (TypeError, ValueError):
        return _DEFAULT_CONNECT_TIMEOUT
    return value if value > 0 else _DEFAULT_CONNECT_TIMEOUT


def dsn_host(dsn: str) -> str:
    """Host part of an Easy Connect string (``[scheme://][user[/pw]@]host[:port][/service]``).

    Returns "" for a connect descriptor, which names no single host.
    """
    rest = dsn.strip().rsplit("@", 1)[-1]
    if rest.startswith("("):
        return ""
    authority = re.split(r"[/?]", rest.split("://", 1)[-1], maxsplit=1)[0]
    if authority.startswith("["):
        return authority[1:].split("]", 1)[0].lower()
    if authority.count(":") > 1:  # bare IPv6 address, no port
        return authority.lower()
    return authority.split(":", 1)[0].lower()


def oracle_config() -> dict | None:
    """Return Oracle connection config, or None when Oracle is not configured.

    Presence of ``CKG_ORACLE_DSN`` is the switch that turns the PGQ path on.
    """
    dsn = os.environ.get("CKG_ORACLE_DSN")
    if not dsn:
        return None
    password = os.environ.get("CKG_ORACLE_PASSWORD", _DEFAULT_PASSWORD)
    # The demo password is only acceptable for a database on this machine.
    if "CKG_ORACLE_PASSWORD" not in os.environ and dsn_host(dsn) not in _LOCAL_HOSTS:
        print(
            "warning: using default CKG_ORACLE_PASSWORD against a non-local DSN — "
            "set CKG_ORACLE_PASSWORD explicitly",
            file=sys.stderr,
        )
    return {
        "dsn": dsn,
        "user": os.environ.get("CKG_ORACLE_USER", "dmuser"),
        "password": password,
        "domain": os.environ.get("CKG_ORACLE_DOMAIN", "default"),
        "graph_name": os.environ.get("CKG_ORACLE_GRAPH", DEFAULT_GRAPH_NAME),
        "table_prefix": os.environ.get("CKG_ORACLE_TABLE_PREFIX", DEFAULT_TABLE_PREFIX),
        "pool_min": _safe_int("CKG_ORACLE_POOL_MIN", 1),
        "pool_max": _safe_int("CKG_ORACLE_POOL_MAX", 4),
        "connect_timeout": _connect_timeout(),
    }


def redact_dsn(dsn: str) -> str:
    """Strip any userinfo (``user[/pass]@``) from a DSN for safe display."""
    return "***@" + dsn.rsplit("@", 1)[1] if "@" in dsn else dsn


def create_pool(cfg: dict):
    """Create an oracledb connection pool from a config dict (lazy import).

    The pool opens connections on first use, so creating it proves nothing
    about reachability; ``connect_pgq`` checks that. The connect timeout and
    the disabled retries keep an unreachable database from stalling a prompt.
    """
    import oracledb

    return oracledb.create_pool(
        user=cfg["user"],
        password=cfg["password"],
        dsn=cfg["dsn"],
        min=cfg["pool_min"],
        max=cfg["pool_max"],
        increment=1,
        tcp_connect_timeout=cfg.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT),
        retry_count=0,
    )


def pgq_mem(pool) -> SimpleNamespace:
    """Wrap an oracledb pool as the object ckg.storage expects (``_pool``)."""
    return SimpleNamespace(_pool=pool)


def _one_line(exc: BaseException, dsn: str) -> str:
    first = (str(exc).splitlines() or [""])[0].strip() or type(exc).__name__
    return first.replace(dsn, redact_dsn(dsn))[:200]


def _close_pool(pool) -> None:
    try:
        pool.close(force=True)
    except Exception:
        pass


def connect_pgq(cfg: dict | None = None):
    """Open a PGQ connection. Returns (mem, cfg) or (None, None).

    Success means a statement actually ran on the database. On failure the
    reason is available from ``last_connect_error()`` and remembered for the
    rest of the process, so later calls return at once.
    """
    global _last_error
    if cfg is None:
        cfg = oracle_config()
    if cfg is None:
        return None, None
    key = (cfg["dsn"], cfg["user"])
    if key in _failed:
        _last_error = _failed[key]
        return None, None
    pool = None
    try:
        pool = create_pool(cfg)
        with pool.acquire() as conn:
            conn.cursor().execute("SELECT 1 FROM DUAL")
    except Exception as exc:
        _last_error = _failed[key] = _one_line(exc, cfg["dsn"])
        if pool is not None:
            _close_pool(pool)
        return None, None
    _last_error = None
    return pgq_mem(pool), cfg


def last_connect_error() -> str | None:
    """Why the most recent ``connect_pgq`` call failed (one line), else None."""
    return _last_error


def close_pgq(mem) -> None:
    """Release the connection pool behind ``mem``; a no-op for None."""
    pool = getattr(mem, "_pool", None)
    if pool is not None:
        _close_pool(pool)


def oracle_summary() -> dict:
    """Best-effort status of the Oracle PGQ path (never raises).

    Returns a dict describing whether Oracle is configured, reachable, and
    what data is stored for the configured domain.
    """
    cfg = oracle_config()
    if cfg is None:
        return {"configured": False, "reason": "CKG_ORACLE_DSN not set (in-memory mode)"}

    summary: dict = {"configured": True, "dsn": redact_dsn(cfg["dsn"]), "domain": cfg["domain"]}
    pool = None
    try:
        pool = create_pool(cfg)
        with pool.acquire() as conn:
            cur = conn.cursor()
            try:
                cur.execute("select banner from v$version where rownum = 1")
                summary["version"] = cur.fetchone()[0]
            except Exception:  # v$version needs a privilege the user may lack
                summary["version"] = f"Oracle Database {conn.version}"
        summary["connected"] = True
    except Exception as exc:
        summary["connected"] = False
        summary["error"] = _one_line(exc, cfg["dsn"])
        if pool is not None:
            _close_pool(pool)
        return summary

    try:
        with pool.acquire() as conn:
            cur = conn.cursor()
            nodes_table, edges_table = _tables(cfg["table_prefix"])
            graph_name = require_identifier(
                cfg["graph_name"], kind="graph name", allow_qualified=True
            )
            cur.execute(f"select count(*) from {nodes_table} where domain = :d", d=cfg["domain"])
            summary["nodes"] = cur.fetchone()[0]
            cur.execute(f"select count(*) from {edges_table} where domain = :d", d=cfg["domain"])
            summary["edges"] = cur.fetchone()[0]
            # Unquoted identifiers are stored upper-case in the dictionary.
            owner, _, name = graph_name.rpartition(".")
            cur.execute(
                "select count(*) from all_property_graphs "
                "where graph_name = :g and owner = nvl(:o, user)",
                g=name.upper(), o=owner.upper() or None,
            )
            summary["property_graph"] = cur.fetchone()[0] > 0
    except Exception as exc:
        summary["stats_error"] = _one_line(exc, cfg["dsn"])
    finally:
        _close_pool(pool)
    return summary
