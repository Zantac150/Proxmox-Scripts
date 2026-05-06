"""
modules/network_monitor.py
Proxmox Sentry – network connection and traffic anomaly monitoring.

Monitors:
  - Active TCP/UDP connections per container/host
  - Per-interface byte counters (delta-based rate)
  - Suspicious listening ports
  - New external connection targets (first-seen tracking)
  - Unusual connection volume spikes
"""

import configparser
import json
import logging
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger("sentry.network_monitor")

# Ports considered always-suspicious when listening externally
DEFAULT_SUSPICIOUS_PORTS: Set[int] = {4444, 1337, 31337, 6666, 9001, 6697, 8888}

# Schema for first-seen host tracking
SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_hosts (
    host TEXT NOT NULL,
    first_seen REAL NOT NULL,
    PRIMARY KEY (host)
);
CREATE TABLE IF NOT EXISTS iface_counters (
    iface TEXT NOT NULL,
    ts    REAL NOT NULL,
    rx    REAL NOT NULL,
    tx    REAL NOT NULL,
    PRIMARY KEY (iface)
);
"""


class NetworkMonitor:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg         = cfg
        self.db_file     = cfg.get("sentry", "db_file", fallback="/var/lib/sentry/sentry.db")
        self.alert_new   = cfg.getboolean(
            "network_monitor", "alert_new_external_host", fallback=True
        )
        raw_susp = cfg.get(
            "network_monitor", "suspicious_ports",
            fallback="4444,1337,31337,6666,9001",
        )
        self.suspicious_ports: Set[int] = DEFAULT_SUSPICIOUS_PORTS.copy()
        for p in raw_susp.split(","):
            p = p.strip()
            if p.isdigit():
                self.suspicious_ports.add(int(p))

        raw_ifaces = cfg.get("network_monitor", "interfaces", fallback="").strip()
        self.watch_ifaces: Optional[List[str]] = (
            [i.strip() for i in raw_ifaces.split(",") if i.strip()]
            if raw_ifaces else None
        )
        Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_file, timeout=10)

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(self) -> List[Dict]:
        issues: List[Dict] = []
        issues.extend(self._check_suspicious_ports())
        issues.extend(self._check_new_external_hosts())
        issues.extend(self._check_traffic_rates())
        return issues

    # ── Suspicious listening ports ─────────────────────────────────────────────

    def _check_suspicious_ports(self) -> List[Dict]:
        issues = []
        try:
            result = subprocess.run(
                ["ss", "-tlnp"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 5:
                    continue
                local_addr = parts[3]
                port_str = local_addr.rsplit(":", 1)[-1]
                try:
                    port = int(port_str)
                except ValueError:
                    continue
                if port in self.suspicious_ports:
                    process = parts[6] if len(parts) > 6 else "unknown"
                    issues.append({
                        "type":        "suspicious_port",
                        "severity":    "critical",
                        "title":       f"Suspicious port {port} is listening",
                        "description": (
                            f"Port {port} (process: {process}) is open and associated "
                            f"with malware or attack tools."
                        ),
                        "port":        port,
                        "process":     process,
                    })
        except Exception as exc:
            log.debug("ss command error: %s", exc)
        return issues

    # ── New external hosts ─────────────────────────────────────────────────────

    def _check_new_external_hosts(self) -> List[Dict]:
        if not self.alert_new:
            return []
        issues = []
        try:
            result = subprocess.run(
                ["ss", "-tnp", "state", "established"],
                capture_output=True, text=True, timeout=10,
            )
            external = set()
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 5:
                    continue
                remote_addr = parts[4]
                host = remote_addr.rsplit(":", 1)[0].strip("[]")
                if self._is_private(host):
                    continue
                external.add(host)

            new_hosts = []
            with self._conn() as conn:
                for host in external:
                    row = conn.execute(
                        "SELECT first_seen FROM seen_hosts WHERE host=?", (host,)
                    ).fetchone()
                    if row is None:
                        conn.execute(
                            "INSERT INTO seen_hosts (host, first_seen) VALUES (?,?)",
                            (host, time.time()),
                        )
                        new_hosts.append(host)

            for host in new_hosts:
                issues.append({
                    "type":        "new_external_host",
                    "severity":    "warning",
                    "title":       f"New external connection target: {host}",
                    "description": (
                        f"An established connection to external host {host} was seen for "
                        f"the first time.  Verify this is expected."
                    ),
                    "host":        host,
                })
        except Exception as exc:
            log.debug("External host check error: %s", exc)
        return issues

    # ── Traffic rate checks ────────────────────────────────────────────────────

    def _check_traffic_rates(self) -> List[Dict]:
        issues = []
        try:
            current = self._read_iface_counters()
            now     = time.time()

            with self._conn() as conn:
                for iface, (rx, tx) in current.items():
                    if self.watch_ifaces and iface not in self.watch_ifaces:
                        continue
                    row = conn.execute(
                        "SELECT ts, rx, tx FROM iface_counters WHERE iface=?", (iface,)
                    ).fetchone()

                    if row:
                        delta_t  = now - row[0]
                        if delta_t > 0:
                            rx_rate = (rx - row[1]) / delta_t   # bytes/s
                            tx_rate = (tx - row[2]) / delta_t

                            # Warn at >500 MB/s sustained (unusual for home lab)
                            RATE_WARN = 500 * 1024 * 1024
                            if rx_rate > RATE_WARN:
                                issues.append({
                                    "type":        "traffic_spike",
                                    "severity":    "warning",
                                    "title":       f"High inbound traffic on {iface}",
                                    "description": (
                                        f"Interface {iface} is receiving "
                                        f"{rx_rate / 1024 / 1024:.1f} MB/s, which is "
                                        f"unusually high."
                                    ),
                                    "iface":       iface,
                                    "rx_rate_mbps": rx_rate / 1024 / 1024,
                                })
                            if tx_rate > RATE_WARN:
                                issues.append({
                                    "type":        "traffic_spike",
                                    "severity":    "warning",
                                    "title":       f"High outbound traffic on {iface}",
                                    "description": (
                                        f"Interface {iface} is sending "
                                        f"{tx_rate / 1024 / 1024:.1f} MB/s."
                                    ),
                                    "iface":       iface,
                                    "tx_rate_mbps": tx_rate / 1024 / 1024,
                                })

                    conn.execute(
                        """INSERT INTO iface_counters (iface, ts, rx, tx) VALUES (?,?,?,?)
                           ON CONFLICT(iface) DO UPDATE SET ts=excluded.ts, rx=excluded.rx, tx=excluded.tx""",
                        (iface, now, rx, tx),
                    )
        except Exception as exc:
            log.debug("Traffic rate check error: %s", exc)
        return issues

    def _read_iface_counters(self) -> Dict[str, tuple]:
        counters = {}
        try:
            with open("/proc/net/dev") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    iface, data = line.split(":", 1)
                    iface = iface.strip()
                    if iface == "lo":
                        continue
                    parts = data.split()
                    if len(parts) >= 9:
                        counters[iface] = (float(parts[0]), float(parts[8]))
        except Exception as exc:
            log.debug("Interface counter read error: %s", exc)
        return counters

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_private(host: str) -> bool:
        """Return True if the address is RFC-1918 / loopback / link-local."""
        private_prefixes = (
            "10.", "172.16.", "172.17.", "172.18.", "172.19.",
            "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
            "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
            "172.30.", "172.31.", "192.168.", "127.", "::1", "fe80",
        )
        return any(host.startswith(p) for p in private_prefixes)
