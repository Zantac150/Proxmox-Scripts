"""
alerting/alertmanager.py
Proxmox Sentry – pluggable alert dispatcher with deduplication and throttling.

Supported channels (enabled via sentry.conf [alerts] channels):
  email      → alerting/channels/email_channel.py
  pushover   → alerting/channels/pushover_channel.py
  webhook    → alerting/channels/webhook_channel.py
  syslog     → alerting/channels/syslog_channel.py

Custom channels can be added by:
  1. Creating alerting/channels/<name>_channel.py implementing the AlertChannel ABC.
  2. Adding the channel name to CHANNEL_REGISTRY below.
  3. Adding it to the [alerts] channels list in sentry.conf.
"""

import configparser
import hashlib
import json
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("sentry.alertmanager")

SEVERITY_LEVEL = {"info": 0, "warning": 1, "critical": 2}

# Schema for alert state tracking (deduplication)
SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_state (
    fingerprint TEXT    NOT NULL PRIMARY KEY,
    last_sent   REAL    NOT NULL,
    count       INTEGER NOT NULL DEFAULT 1
);
"""


# ── Channel base class ─────────────────────────────────────────────────────────

class AlertChannel(ABC):
    """All alert channels must implement this interface."""

    @abstractmethod
    def send(self, alert: Dict) -> bool:
        """Send the alert.  Return True on success, False on failure."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable channel name, e.g. 'email'."""


# ── Channel registry ───────────────────────────────────────────────────────────

def _load_channel(name: str, cfg: configparser.ConfigParser) -> Optional[AlertChannel]:
    """Dynamically import and instantiate a named alert channel."""
    import importlib
    module_name = f"alerting.channels.{name}_channel"
    class_name  = f"{name.capitalize()}Channel"
    try:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        return cls(cfg)
    except (ImportError, AttributeError) as exc:
        log.error("Cannot load alert channel '%s': %s", name, exc)
        return None


# ── Alert manager ──────────────────────────────────────────────────────────────

class AlertManager:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg          = cfg
        self.db_file      = cfg.get("sentry", "db_file", fallback="/var/lib/sentry/sentry.db")
        self.dedup_window = cfg.getint("alerts", "dedup_seconds", fallback=3600)
        self.min_severity = cfg.get("sentry", "min_alert_severity", fallback="warning").lower()

        Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

        # Initialise enabled channels
        raw_channels = cfg.get("alerts", "channels", fallback="").strip()
        self.channels: List[AlertChannel] = []
        for name in (n.strip() for n in raw_channels.split(",") if n.strip()):
            ch = _load_channel(name, cfg)
            if ch is not None:
                self.channels.append(ch)
                log.info("Alert channel loaded: %s", name)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_file, timeout=10)

    # ── Fingerprinting (dedup) ─────────────────────────────────────────────────

    @staticmethod
    def _fingerprint(alert: Dict) -> str:
        """Stable hash of source + title — used for deduplication."""
        key = f"{alert.get('source', '')}|{alert.get('title', '')}|{alert.get('severity', '')}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _is_suppressed(self, fp: str) -> bool:
        """Return True if this alert fingerprint was recently sent."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_sent FROM alert_state WHERE fingerprint=?", (fp,)
            ).fetchone()
        if row is None:
            return False
        return (time.time() - row[0]) < self.dedup_window

    def _record_sent(self, fp: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO alert_state (fingerprint, last_sent, count) VALUES (?,?,1)
                   ON CONFLICT(fingerprint) DO UPDATE
                   SET last_sent=excluded.last_sent, count=count+1""",
                (fp, time.time()),
            )

    # ── Severity filtering ─────────────────────────────────────────────────────

    def _meets_min_severity(self, alert: Dict) -> bool:
        sev     = alert.get("severity", "info").lower()
        min_lvl = SEVERITY_LEVEL.get(self.min_severity, 1)
        cur_lvl = SEVERITY_LEVEL.get(sev, 0)
        return cur_lvl >= min_lvl

    # ── Dispatch ───────────────────────────────────────────────────────────────

    def dispatch(self, alert: Dict):
        """Route an alert through all configured channels after dedup checks."""
        if not self._meets_min_severity(alert):
            log.debug("Alert below min severity (%s); suppressed.", alert.get("severity"))
            return

        fp = self._fingerprint(alert)
        if self._is_suppressed(fp):
            log.debug("Alert suppressed (dedup): %s", alert.get("title"))
            return

        self._record_sent(fp)

        if not self.channels:
            log.warning(
                "No alert channels configured.  Alert: [%s] %s",
                alert.get("severity", "?").upper(),
                alert.get("title", ""),
            )
            return

        for ch in self.channels:
            try:
                ok = ch.send(alert)
                if ok:
                    log.debug("Alert sent via %s: %s", ch.name, alert.get("title"))
                else:
                    log.warning("Alert channel %s returned failure for: %s", ch.name, alert.get("title"))
            except Exception as exc:
                log.error("Alert channel %s raised an exception: %s", ch.name, exc)

    # ── Format helpers (used by channels) ─────────────────────────────────────

    @staticmethod
    def format_text(alert: Dict) -> str:
        """Render an alert dict as a plain-text message."""
        lines = [
            f"[{alert.get('severity', 'info').upper()}] {alert.get('title', 'Sentry Alert')}",
            "",
            f"Source      : {alert.get('source', 'unknown')}",
            f"Description : {alert.get('description', '')}",
        ]
        rec = alert.get("recommendation", "")
        if rec:
            lines += ["", "Recommendation:", rec]
        return "\n".join(lines)

    @staticmethod
    def format_html(alert: Dict) -> str:
        """Render an alert dict as an HTML message."""
        sev   = alert.get("severity", "info").upper()
        color = {"CRITICAL": "#d32f2f", "WARNING": "#f57c00", "INFO": "#1976d2"}.get(sev, "#555")
        rec   = alert.get("recommendation", "")
        rec_block = f"<p><b>Recommendation:</b><br>{rec}</p>" if rec else ""
        return f"""<html><body>
<h2 style="color:{color}">[{sev}] {alert.get('title','Sentry Alert')}</h2>
<p><b>Source:</b> {alert.get('source','unknown')}</p>
<p><b>Description:</b><br>{alert.get('description','')}</p>
{rec_block}
</body></html>"""
