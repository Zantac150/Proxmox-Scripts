"""
modules/baseline.py
Proxmox Sentry – metric collection and ML baseline management.

Collects system-level and per-container/VM metrics from the Proxmox API
(and /proc on the local host), stores them in SQLite, and fits scikit-learn
models for the anomaly detection pipeline.
"""

import configparser
import json
import logging
import pickle
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    from proxmoxer import ProxmoxAPI
    PROXMOXER_AVAILABLE = True
except ImportError:
    PROXMOXER_AVAILABLE = False

try:
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

log = logging.getLogger("sentry.baseline")

# ── Database schema ────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    source      TEXT    NOT NULL,
    vmid        TEXT,
    metric      TEXT    NOT NULL,
    value       REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts     ON metrics(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_source ON metrics(source, metric);

CREATE TABLE IF NOT EXISTS models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    created_at  REAL    NOT NULL,
    blob        BLOB    NOT NULL
);
"""


class BaselineCollector:
    """Collect metrics and maintain rolling ML baseline models."""

    def __init__(self, cfg: configparser.ConfigParser, db_file: str):
        self.cfg        = cfg
        self.db_file    = db_file
        self.baseline_days = cfg.getint("sentry", "baseline_days", fallback=7)
        self._pve: Optional[Any] = None
        self._init_db()

    # ── Database ───────────────────────────────────────────────────────────────

    def _init_db(self):
        Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_file, timeout=10)

    # ── Proxmox API connection ─────────────────────────────────────────────────

    def _pve_connect(self) -> Optional[Any]:
        if not PROXMOXER_AVAILABLE:
            log.warning("proxmoxer not available; Proxmox API metrics disabled.")
            return None
        if self._pve is not None:
            return self._pve
        try:
            token_name  = self.cfg.get("proxmox", "token_name", fallback="").strip()
            token_value = self.cfg.get("proxmox", "token_value", fallback="").strip()
            verify_ssl  = self.cfg.getboolean("proxmox", "verify_ssl", fallback=False)
            host        = self.cfg.get("proxmox", "host", fallback="127.0.0.1")
            user        = self.cfg.get("proxmox", "user", fallback="root@pam")

            if token_name and token_value:
                self._pve = ProxmoxAPI(
                    host, user=user,
                    token_name=token_name, token_value=token_value,
                    verify_ssl=verify_ssl,
                )
            else:
                pw = self.cfg.get("proxmox", "password", fallback="")
                self._pve = ProxmoxAPI(
                    host, user=user, password=pw, verify_ssl=verify_ssl
                )
            log.debug("Connected to Proxmox API at %s", host)
        except Exception as exc:
            log.error("Failed to connect to Proxmox API: %s", exc)
            self._pve = None
        return self._pve

    # ── Metric collection ──────────────────────────────────────────────────────

    def collect(self) -> Dict[str, float]:
        """Collect a snapshot of metrics.  Returns {metric_key: value}."""
        metrics: Dict[str, float] = {}
        metrics.update(self._collect_host_metrics())
        metrics.update(self._collect_pve_metrics())
        return metrics

    def _collect_host_metrics(self) -> Dict[str, float]:
        m: Dict[str, float] = {}
        try:
            # CPU load
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                m["host.load1"]  = float(parts[0])
                m["host.load5"]  = float(parts[1])
                m["host.load15"] = float(parts[2])

            # Memory
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    mem[k.strip()] = int(v.split()[0])
            total = mem.get("MemTotal", 1)
            avail = mem.get("MemAvailable", total)
            m["host.mem_used_pct"] = 100.0 * (total - avail) / total if total else 0.0

            # Network bytes
            with open("/proc/net/dev") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) != 2:
                        continue
                    iface = parts[0].strip()
                    if iface in ("lo",):
                        continue
                    vals = parts[1].split()
                    m[f"net.{iface}.rx_bytes"] = float(vals[0])
                    m[f"net.{iface}.tx_bytes"] = float(vals[8])

            # Disk IO (first device)
            with open("/proc/diskstats") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 14:
                        continue
                    dev = parts[2]
                    if dev.startswith(("sd", "vd", "nvme", "xvd")):
                        m[f"disk.{dev}.reads"]  = float(parts[3])
                        m[f"disk.{dev}.writes"] = float(parts[7])
                        break
        except Exception as exc:
            log.debug("Host metric collection partial error: %s", exc)
        return m

    def _collect_pve_metrics(self) -> Dict[str, float]:
        m: Dict[str, float] = {}
        pve = self._pve_connect()
        if pve is None:
            return m
        try:
            node = pve.nodes.get()[0]["node"]
            status = pve.nodes(node).status.get()
            m["pve.cpu_pct"]   = float(status.get("cpu", 0.0)) * 100
            mem_info = status.get("memory", {})
            mem_total = mem_info.get("total", 1)
            mem_used  = mem_info.get("used", 0)
            m["pve.mem_used_pct"] = 100.0 * mem_used / mem_total if mem_total else 0.0

            # Per-container/VM
            for guest in pve.nodes(node).lxc.get() + pve.nodes(node).qemu.get():
                vmid = str(guest["vmid"])
                if guest.get("status") != "running":
                    continue
                try:
                    kind = "lxc" if "lxc" in str(type(guest)).lower() else "qemu"
                    rrd = (
                        pve.nodes(node).lxc(vmid).rrddata.get(timeframe="hour", cf="AVERAGE")
                        if kind == "lxc"
                        else pve.nodes(node).qemu(vmid).rrddata.get(timeframe="hour", cf="AVERAGE")
                    )
                    if rrd:
                        last = rrd[-1]
                        m[f"vm.{vmid}.cpu_pct"]      = float(last.get("cpu", 0.0)) * 100
                        m[f"vm.{vmid}.mem_used_pct"] = (
                            100.0 * float(last.get("mem", 0)) / float(last.get("maxmem", 1))
                            if last.get("maxmem") else 0.0
                        )
                        m[f"vm.{vmid}.net_in"]  = float(last.get("netin", 0))
                        m[f"vm.{vmid}.net_out"] = float(last.get("netout", 0))
                except Exception:
                    pass
        except Exception as exc:
            log.debug("PVE API metric collection error: %s", exc)
        return m

    # ── Persistence ────────────────────────────────────────────────────────────

    def store(self, metrics: Dict[str, float]):
        """Persist a metrics snapshot to SQLite."""
        ts = time.time()
        rows = []
        for key, value in metrics.items():
            parts = key.split(".", 1)
            source = parts[0]
            metric = parts[1] if len(parts) > 1 else key
            # Extract vmid for vm.* metrics
            vmid = None
            if source == "vm" and "." in metric:
                vmid_part, metric = metric.split(".", 1)
                vmid = vmid_part
            rows.append((ts, source, vmid, metric, value))

        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO metrics (ts, source, vmid, metric, value) VALUES (?,?,?,?,?)",
                rows,
            )
        self._prune_old_metrics()

    def _prune_old_metrics(self):
        cutoff = time.time() - self.baseline_days * 86400
        with self._conn() as conn:
            conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))

    # ── Retrieve history ───────────────────────────────────────────────────────

    def get_history(self, source: str, metric: str, days: Optional[int] = None) -> np.ndarray:
        """Return a 1-D array of historical values for the given metric."""
        lookback = (days or self.baseline_days) * 86400
        cutoff   = time.time() - lookback
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT value FROM metrics WHERE source=? AND metric=? AND ts >= ? ORDER BY ts",
                (source, metric, cutoff),
            ).fetchall()
        return np.array([r[0] for r in rows], dtype=float)

    def get_all_metric_keys(self) -> List[str]:
        """Return all distinct (source, metric) pairs currently in the DB."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source, metric FROM metrics"
            ).fetchall()
        return [f"{r[0]}.{r[1]}" for r in rows]

    # ── Model persistence ──────────────────────────────────────────────────────

    def save_model(self, name: str, model: Any):
        blob = pickle.dumps(model)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO models (name, created_at, blob)
                   VALUES (?,?,?)
                   ON CONFLICT(name) DO UPDATE SET created_at=excluded.created_at, blob=excluded.blob""",
                (name, time.time(), blob),
            )

    def load_model(self, name: str) -> Optional[Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT blob FROM models WHERE name=?", (name,)
            ).fetchone()
        if row:
            return pickle.loads(row[0])
        return None

    # ── Feature matrix builder ─────────────────────────────────────────────────

    def build_feature_matrix(self, metric_keys: List[str]) -> Optional[np.ndarray]:
        """
        Build an (N_samples × N_features) matrix aligned by timestamp bucket
        (5-minute buckets), for model training.
        """
        if not SKLEARN_AVAILABLE:
            log.warning("scikit-learn not available; cannot build feature matrix.")
            return None

        bucket_seconds = 300
        cutoff = time.time() - self.baseline_days * 86400
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, source, metric, vmid, value FROM metrics WHERE ts >= ?", (cutoff,)
            ).fetchall()

        if not rows:
            return None

        # Group into time buckets
        bucket_map: Dict[int, Dict[str, float]] = {}
        for ts, source, metric, vmid, value in rows:
            bucket = int(ts // bucket_seconds)
            key_parts = [source]
            if vmid:
                key_parts.append(vmid)
            key_parts.append(metric)
            key = ".".join(key_parts)
            if bucket not in bucket_map:
                bucket_map[bucket] = {}
            bucket_map[bucket][key] = value

        buckets = sorted(bucket_map.keys())
        matrix = []
        for b in buckets:
            row_vals = [bucket_map[b].get(k, 0.0) for k in metric_keys]
            matrix.append(row_vals)

        return np.array(matrix, dtype=float) if matrix else None
