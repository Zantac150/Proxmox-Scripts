"""
alerting/channels/email_channel.py
Proxmox Sentry – SMTP email alert channel.

Configuration (sentry.conf [email] section):
  enabled         = true
  smtp_host       = smtp.example.com
  smtp_port       = 587
  use_tls         = true
  smtp_user       = alerts@example.com
  smtp_pass       = secretpassword
  from            = sentry@proxmox.local
  to              = admin@example.com, oncall@example.com
  subject_prefix  = [Proxmox Sentry]
"""

import configparser
import logging
import smtplib
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List

from alerting.alertmanager import AlertChannel, AlertManager

log = logging.getLogger("sentry.channel.email")


class EmailChannel(AlertChannel):
    name = "email"

    def __init__(self, cfg: configparser.ConfigParser):
        section = "email"
        self.enabled    = cfg.getboolean(section, "enabled", fallback=False)
        self.smtp_host  = cfg.get(section, "smtp_host", fallback="localhost")
        self.smtp_port  = cfg.getint(section, "smtp_port", fallback=25)
        self.use_tls    = cfg.getboolean(section, "use_tls", fallback=False)
        self.smtp_user  = cfg.get(section, "smtp_user", fallback="").strip()
        self.smtp_pass  = cfg.get(section, "smtp_pass", fallback="").strip()
        self.from_addr  = cfg.get(section, "from", fallback="sentry@proxmox.local")
        raw_to          = cfg.get(section, "to", fallback="").strip()
        self.to_addrs: List[str] = [a.strip() for a in raw_to.split(",") if a.strip()]
        self.subject_prefix = cfg.get(section, "subject_prefix",
                                      fallback="[Proxmox Sentry]").strip()

    def send(self, alert: Dict) -> bool:
        if not self.enabled:
            return False
        if not self.to_addrs:
            log.warning("Email channel: no recipient addresses configured.")
            return False

        sev     = alert.get("severity", "info").upper()
        subject = f"{self.subject_prefix} [{sev}] {alert.get('title', 'Sentry Alert')}"

        msg = MIMEMultipart("alternative")
        msg["From"]    = self.from_addr
        msg["To"]      = ", ".join(self.to_addrs)
        msg["Subject"] = subject

        plain = AlertManager.format_text(alert)
        html  = AlertManager.format_html(alert)
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        try:
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10)

            if self.smtp_user and self.smtp_pass:
                server.login(self.smtp_user, self.smtp_pass)

            server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            server.quit()
            log.info("Email alert sent to %s: %s", self.to_addrs, subject)
            return True
        except (smtplib.SMTPException, OSError, socket.error) as exc:
            log.error("Email send failed: %s", exc)
            return False
