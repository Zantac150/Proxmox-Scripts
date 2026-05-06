"""
alerting/channels/syslog_channel.py
Proxmox Sentry – syslog / remote log-platform alert channel.

Supports:
  - Local Unix socket syslog (rsyslog, journald)
  - Remote UDP/TCP syslog (Graylog, Loki, Splunk syslog input, etc.)

Configuration (sentry.conf [syslog] section):
  enabled       = true
  mode          = local        # local | remote
  remote_host   = 192.168.1.20
  remote_port   = 514
  protocol      = udp          # udp | tcp
  facility      = LOG_LOCAL0
  tag           = sentry
"""

import configparser
import logging
import logging.handlers
import socket
from typing import Dict

from alerting.alertmanager import AlertChannel, AlertManager

log = logging.getLogger("sentry.channel.syslog")

# Map from sentry severity to syslog level
SEVERITY_SYSLOG = {
    "info":     logging.INFO,
    "warning":  logging.WARNING,
    "critical": logging.CRITICAL,
}

FACILITY_MAP = {
    "LOG_USER":   logging.handlers.SysLogHandler.LOG_USER,
    "LOG_LOCAL0": logging.handlers.SysLogHandler.LOG_LOCAL0,
    "LOG_LOCAL1": logging.handlers.SysLogHandler.LOG_LOCAL1,
    "LOG_LOCAL2": logging.handlers.SysLogHandler.LOG_LOCAL2,
    "LOG_LOCAL3": logging.handlers.SysLogHandler.LOG_LOCAL3,
    "LOG_LOCAL4": logging.handlers.SysLogHandler.LOG_LOCAL4,
    "LOG_LOCAL5": logging.handlers.SysLogHandler.LOG_LOCAL5,
    "LOG_LOCAL6": logging.handlers.SysLogHandler.LOG_LOCAL6,
    "LOG_LOCAL7": logging.handlers.SysLogHandler.LOG_LOCAL7,
    "LOG_DAEMON": logging.handlers.SysLogHandler.LOG_DAEMON,
    "LOG_SYSLOG": logging.handlers.SysLogHandler.LOG_SYSLOG,
    "LOG_KERN":   logging.handlers.SysLogHandler.LOG_KERN,
}


class SyslogChannel(AlertChannel):
    name = "syslog"

    def __init__(self, cfg: configparser.ConfigParser):
        section         = "syslog"
        self.enabled    = cfg.getboolean(section, "enabled", fallback=False)
        self.mode       = cfg.get(section, "mode", fallback="local").lower()
        self.remote_host = cfg.get(section, "remote_host", fallback="127.0.0.1")
        self.remote_port = cfg.getint(section, "remote_port", fallback=514)
        protocol_str    = cfg.get(section, "protocol", fallback="udp").lower()
        self.socktype   = socket.SOCK_DGRAM if protocol_str == "udp" else socket.SOCK_STREAM
        facility_str    = cfg.get(section, "facility", fallback="LOG_LOCAL0").upper()
        self.facility   = FACILITY_MAP.get(facility_str, logging.handlers.SysLogHandler.LOG_LOCAL0)
        self.tag        = cfg.get(section, "tag", fallback="sentry").strip()
        self._handler   = None

    def _get_handler(self) -> logging.handlers.SysLogHandler:
        if self._handler is not None:
            return self._handler
        if self.mode == "remote":
            address = (self.remote_host, self.remote_port)
        else:
            address = "/dev/log"
        handler = logging.handlers.SysLogHandler(address=address, facility=self.facility,
                                                  socktype=self.socktype)
        fmt = logging.Formatter(f"{self.tag}: %(message)s")
        handler.setFormatter(fmt)
        self._handler = handler
        return handler

    def send(self, alert: Dict) -> bool:
        if not self.enabled:
            return False

        sev      = alert.get("severity", "info").lower()
        level    = SEVERITY_SYSLOG.get(sev, logging.WARNING)
        message  = AlertManager.format_text(alert)

        try:
            handler = self._get_handler()
            record  = logging.LogRecord(
                name=self.tag, level=level,
                pathname="", lineno=0,
                msg=message, args=(), exc_info=None,
            )
            handler.emit(record)
            log.debug("Syslog alert emitted (level=%d): %s", level, alert.get("title"))
            return True
        except Exception as exc:
            log.error("Syslog send failed: %s", exc)
            # Reset handler so it is recreated on next attempt
            self._handler = None
            return False
