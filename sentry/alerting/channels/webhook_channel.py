"""
alerting/channels/webhook_channel.py
Proxmox Sentry – generic webhook alert channel.

Supports Slack, Discord, Microsoft Teams, and any JSON webhook receiver.
Set 'format' in sentry.conf to match the target platform.

Configuration (sentry.conf [webhook] section):
  enabled          = true
  url              = https://hooks.slack.com/services/T.../B.../...
  secret           = (optional) HMAC-SHA256 signing secret
  signature_header = X-Hub-Signature-256
  format           = slack        # slack | discord | teams | json
  min_severity     = warning
"""

import configparser
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import requests

from alerting.alertmanager import AlertChannel, AlertManager, SEVERITY_LEVEL

log = logging.getLogger("sentry.channel.webhook")

SEVERITY_COLOR = {
    "info":     "#1976d2",
    "warning":  "#f57c00",
    "critical": "#d32f2f",
}
SEVERITY_EMOJI = {
    "info":     "ℹ️",
    "warning":  "⚠️",
    "critical": "🚨",
}


class WebhookChannel(AlertChannel):
    name = "webhook"

    def __init__(self, cfg: configparser.ConfigParser):
        section         = "webhook"
        self.enabled    = cfg.getboolean(section, "enabled", fallback=False)
        self.url        = cfg.get(section, "url", fallback="").strip()
        self.secret     = cfg.get(section, "secret", fallback="").strip()
        self.sig_header = cfg.get(section, "signature_header", fallback="").strip()
        self.format     = cfg.get(section, "format", fallback="json").lower()
        min_sev         = cfg.get(section, "min_severity", fallback="warning").lower()
        self.min_level  = SEVERITY_LEVEL.get(min_sev, 1)

    def send(self, alert: Dict) -> bool:
        if not self.enabled:
            return False
        if not self.url:
            log.warning("Webhook channel: no URL configured.")
            return False

        sev_level = SEVERITY_LEVEL.get(alert.get("severity", "info").lower(), 0)
        if sev_level < self.min_level:
            return True  # silently suppressed by min_severity

        body = self._build_payload(alert)
        headers = {"Content-Type": "application/json"}

        raw = json.dumps(body)
        if self.secret and self.sig_header:
            sig = hmac.new(
                self.secret.encode(),
                raw.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers[self.sig_header] = f"sha256={sig}"

        try:
            resp = requests.post(self.url, data=raw, headers=headers, timeout=10)
            resp.raise_for_status()
            log.info("Webhook alert sent (%s): %s", self.format, alert.get("title"))
            return True
        except requests.RequestException as exc:
            log.error("Webhook send failed: %s", exc)
            return False

    def _build_payload(self, alert: Dict) -> Any:
        sev   = alert.get("severity", "info").lower()
        title = alert.get("title", "Sentry Alert")
        desc  = alert.get("description", "")
        rec   = alert.get("recommendation", "")
        src   = alert.get("source", "unknown")
        emoji = SEVERITY_EMOJI.get(sev, "")
        color = SEVERITY_COLOR.get(sev, "#555")

        if self.format == "slack":
            return self._slack_payload(title, desc, rec, src, sev, color, emoji)
        if self.format == "discord":
            return self._discord_payload(title, desc, rec, src, sev, color, emoji)
        if self.format == "teams":
            return self._teams_payload(title, desc, rec, src, sev, color)
        # Generic JSON
        return {
            "source":         src,
            "severity":       sev,
            "title":          title,
            "description":    desc,
            "recommendation": rec,
        }

    @staticmethod
    def _slack_payload(title, desc, rec, src, sev, color, emoji) -> Dict:
        fields = [
            {"type": "mrkdwn", "text": f"*Source:* {src}"},
            {"type": "mrkdwn", "text": f"*Severity:* {sev.upper()}"},
        ]
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}"}},
            {"type": "section", "fields": fields},
            {"type": "section", "text": {"type": "mrkdwn", "text": desc}},
        ]
        if rec:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Recommendation:*\n{rec}"},
            })
        return {"blocks": blocks}

    @staticmethod
    def _discord_payload(title, desc, rec, src, sev, color, emoji) -> Dict:
        hex_color = int(color.lstrip("#"), 16)
        fields = [{"name": "Source", "value": src, "inline": True},
                  {"name": "Severity", "value": sev.upper(), "inline": True}]
        if rec:
            fields.append({"name": "Recommendation", "value": rec[:1024], "inline": False})
        return {
            "embeds": [{
                "title":       f"{emoji} {title}",
                "description": desc[:4096],
                "color":       hex_color,
                "fields":      fields,
            }]
        }

    @staticmethod
    def _teams_payload(title, desc, rec, src, sev, color) -> Dict:
        facts = [
            {"name": "Source", "value": src},
            {"name": "Severity", "value": sev.upper()},
        ]
        sections = [{"activityTitle": title, "activitySubtitle": desc, "facts": facts}]
        if rec:
            sections.append({"activityTitle": "Recommendation", "activityText": rec})
        return {
            "@type":      "MessageCard",
            "@context":   "https://schema.org/extensions",
            "themeColor": color.lstrip("#"),
            "summary":    title,
            "sections":   sections,
        }
