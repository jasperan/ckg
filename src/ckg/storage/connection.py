"""Oracle AI Database 26ai Free connectivity for CKG.

Reads connection settings from the environment (CKG_ORACLE_*), creates an
oracledb pool, and exposes the tiny ``.pool`` shim the rest of ckg's storage
layer expects (``mem._pool``). Oracle is fully optional: when no
``CKG_ORACLE_DSN`` is set, ckg runs in pure in-memory mode.

Environment variables:
    CKG_ORACLE_DSN            e.g. "localhost:1521/FREEPDB1" (unset → memory mode)
    CKG_ORACLE_USER           default "dmuser"
    CKG_ORACLE_PASSWORD       default "continual_learning"
    CKG_ORACLE_DOMAIN         PGQ domain scope, default "default"
    CKG_ORACLE_GRAPH          property graph name, default "ckg_code_graph"
    CKG_ORACLE_TABLE_PREFIX   node/edge table prefix, default "MEMORY_GRAPH"
    CKG_ORACLE_POOL_MIN/MAX   pool sizing, default 1/4
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from ckg.storage.oracle_pgq import DEFAULT_GRAPH_NAME


def oracle_config() -> dict | None:
    """Return Oracle connection config, or None when Oracle is not configured.

    Presence of ``CKG_ORACLE_DSN`` is the switch that turns the PGQ path on.
    """
    dsn = os.environ.get("CKG_ORACLE_DSN")
    if not dsn:
        return None
    return {
        "dsn": dsn,
        "user": os.environ.get("CKG_ORACLE_USER", "dmuser"),
        "password": os.environ.get("CKG_ORACLE_PASSWORD", "continual_learning"),
        "domain": os.environ.get("CKG_ORACLE_DOMAIN", "default"),
        "graph_name": os.environ.get("CKG_ORACLE_GRAPH", DEFAULT_GRAPH_NAME),
        "table_prefix": os.environ.get("CKG_ORACLE_TABLE_PREFIX", "MEMORY_GRAPH"),
        "pool_min": int(os.environ.get("CKG_ORACLE_POOL_MIN", "1")),
        "pool_max": int(os.environ.get("CKG_ORACLE_POOL_MAX", "4")),
    }


def create_pool(cfg: dict):
    """Create an oracledb connection pool from a config dict (lazy import)."""
    import oracledb

    return oracledb.create_pool(
        user=cfg["user"],
        password=cfg["password"],
        dsn=cfg["dsn"],
        min=cfg["pool_min"],
        max=cfg["pool_max"],
        increment=1,
    )


def pgq_mem(pool) -> SimpleNamespace:
    """Wrap an oracledb pool as the object ckg.storage expects (``_pool``)."""
    return SimpleNamespace(_pool=pool)


def connect_pgq(cfg: dict | None = None):
    """Open a PGQ connection. Returns (mem, cfg) or (None, None).

    Returns None when Oracle is not configured OR cannot be reached — the
    caller decides whether to fall back to in-memory mode.
    """
    if cfg is None:
        cfg = oracle_config()
    if cfg is None:
        return None, None
    try:
        pool = create_pool(cfg)
        return pgq_mem(pool), cfg
    except Exception:
        return None, None


def oracle_summary() -> dict:
    """Best-effort status of the Oracle PGQ path (never raises).

    Returns a dict describing whether Oracle is configured, reachable, and
    what data is stored for the configured domain.
    """
    cfg = oracle_config()
    if cfg is None:
        return {"configured": False, "reason": "CKG_ORACLE_DSN not set (in-memory mode)"}

    summary: dict = {"configured": True, "dsn": cfg["dsn"], "domain": cfg["domain"]}
    mem = None
    try:
        pool = create_pool(cfg)
        mem = pgq_mem(pool)
        with pool.acquire() as conn:
            cur = conn.cursor()
            cur.execute("select banner from v$version where rownum = 1")
            summary["version"] = cur.fetchone()[0]
        summary["connected"] = True
    except Exception as exc:  # pragma: no cover - depends on live DB
        summary["connected"] = False
        summary["error"] = str(exc).splitlines()[0][:200]
        return summary

    try:
        with pool.acquire() as conn:
            cur = conn.cursor()
            prefix = cfg["table_prefix"]
            cur.execute(
                f"select count(*) from {prefix}_NODES where domain = :d",
                d=cfg["domain"],
            )
            summary["nodes"] = cur.fetchone()[0]
            cur.execute(
                f"select count(*) from {prefix}_EDGES where domain = :d",
                d=cfg["domain"],
            )
            summary["edges"] = cur.fetchone()[0]
            cur.execute(
                f"select count(*) from user_property_graphs "
                f"where graph_name = :g",
                g=cfg["graph_name"],
            )
            summary["property_graph"] = cur.fetchone()[0] > 0
    except Exception as exc:  # pragma: no cover - depends on live DB
        summary["stats_error"] = str(exc).splitlines()[0][:200]
    finally:
        try:
            pool.close()
        except Exception:
            pass
    return summary
