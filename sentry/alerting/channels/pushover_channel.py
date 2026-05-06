"""
alerting/channels/pushover_channel.py
Proxmox Sentry – Pushover mobile push notification channel.

Pushover docs: https://pushover.net/api

Configuration (sentry.conf [pushover] section):
  enabled   = true
  api_token = <application token from pushover.net>
  user_key  = <your user/group key>
  priority  = 0    # -2 lowest / -1 low / 0 normal / 1 high / 2 emergency
"""

import configparser
import logging
from typing import Dict

import requests

from alerting.alertmanager import AlertChannel

log = logging.getLogger("sentry.channel.pushover")

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
SEVERITY_PRIORITY = {
    "info":     -1,
    "warning":   0,
    "critical":  1,
}


class PushoverChannel(AlertChannel):
    name = "pushover"

    def __init__(self, cfg: configparser.ConfigParser):
        section       = "pushover"
        self.enabled   = cfg.getboolean(section, "enabled", fallback=False)
        self.api_token = cfg.get(section, "api_token", fallback="").strip()
        self.user_key  = cfg.get(section, "user_key", fallback="").strip()
        self.base_priority = cfg.getint(section, "priority", fallback=0)

    def send(self, alert: Dict) -> bool:
        if not self.enabled:
            return False
        if not self.api_token or not self.user_key:
            log.warning("Pushover channel: api_token or user_key not configured.")
            return False

        sev      = alert.get("severity", "info").lower()
        priority = SEVERITY_PRIORITY.get(sev, self.base_priority)

        title   = f"[{sev.upper()}] {alert.get('title', 'Sentry Alert')}"
        message = alert.get("description", "")
        rec     = alert.get("recommendation", "")
        if rec:
            message += f"\n\nRecommendation: {rec}"
        # Pushover message limit is 1024 characters
        message = message[:1024]

        payload: Dict = {
            "token":    self.api_token,
            "user":     self.user_key,
            "title":    title[:250],
            "message":  message,
            "priority": priority,
        }

        # Emergency priority requires retry/expire
        if priority == 2:
            payload["retry"]  = 60
            payload["expire"] = 3600

        try:
            resp = requests.post(PUSHOVER_API_URL, data=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if result.get("status") == 1:
                log.info("Pushover alert sent: %s", title)
                return True
            log.error("Pushover API error: %s", result.get("errors"))
            return False
        except requests.RequestException as exc:
            log.error("Pushover send failed: %s", exc)
            return False
