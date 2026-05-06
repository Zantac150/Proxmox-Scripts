#!/usr/bin/env python3
"""
sentry-agent.py
Proxmox Sentry – main orchestration daemon.

Loads configuration, schedules module runs, aggregates findings,
and dispatches alerts through the configured alerting channels.

Usage:
    python3 sentry-agent.py [--config /path/to/sentry.conf]
    python3 sentry-agent.py --help
"""

import argparse
import configparser
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

# ── Local imports ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from modules.baseline import BaselineCollector
from modules.anomaly_detector import AnomalyDetector
from modules.network_monitor import NetworkMonitor
from modules.vuln_scanner import VulnScanner
from modules.config_auditor import ConfigAuditor
from modules.recommender import Recommender
from alerting.alertmanager import AlertManager

SENTRY_VERSION = "1.0.0"
DEFAULT_CONFIG = "/opt/sentry/config/sentry.conf"

# ── Logging setup ──────────────────────────────────────────────────────────────

def setup_logging(log_file: str) -> logging.Logger:
    logger = logging.getLogger("sentry")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file handler
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


# ── Configuration ──────────────────────────────────────────────────────────────

def load_config(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict({
        "proxmox":       {"host": "127.0.0.1", "user": "root@pam", "password": "",
                          "token_name": "", "token_value": "", "verify_ssl": "false"},
        "sentry":        {"interval_seconds": "300", "baseline_days": "7",
                          "log_file": "/var/log/sentry/sentry.log",
                          "db_file": "/var/lib/sentry/sentry.db",
                          "anomaly_threshold": "0.10",
                          "min_alert_severity": "warning",
                          "exclude_ids": ""},
        "alerts":        {"channels": "email,pushover", "dedup_seconds": "3600"},
        "email":         {"enabled": "false", "smtp_host": "localhost", "smtp_port": "25",
                          "use_tls": "false", "smtp_user": "", "smtp_pass": "",
                          "from": "sentry@proxmox.local", "to": "",
                          "subject_prefix": "[Proxmox Sentry]"},
        "pushover":      {"enabled": "false", "api_token": "", "user_key": "", "priority": "0"},
        "webhook":       {"enabled": "false", "url": "", "secret": "",
                          "signature_header": "", "format": "json", "min_severity": "warning"},
        "syslog":        {"enabled": "false", "mode": "local",
                          "remote_host": "127.0.0.1", "remote_port": "514",
                          "protocol": "udp", "facility": "LOG_LOCAL0", "tag": "sentry"},
        "vuln_scanner":  {"enabled": "true", "scan_interval_seconds": "86400",
                          "min_severity": "HIGH", "ignore_unfixed": "true",
                          "trivy_bin": "trivy"},
        "network_monitor": {"enabled": "true", "interfaces": "",
                             "suspicious_ports": "4444,1337,31337,6666,9001",
                             "alert_new_external_host": "true"},
        "config_auditor": {"enabled": "true", "check_privileged": "true",
                           "check_host_devices": "true", "check_rootfs_perms": "true",
                           "min_score_threshold": "70"},
    })

    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    cfg.read(path)
    return cfg


# ── Agent ──────────────────────────────────────────────────────────────────────

class SentryAgent:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg = cfg
        self.log = logging.getLogger("sentry.agent")

        # Shared exclude list
        raw_excl = cfg.get("sentry", "exclude_ids", fallback="")
        self.exclude_ids = set(
            x.strip() for x in raw_excl.split(",") if x.strip()
        )

        # Component initialisation
        self.db_file   = cfg.get("sentry", "db_file")
        Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)

        self.alertmgr  = AlertManager(cfg)
        self.baseline  = BaselineCollector(cfg, self.db_file)
        self.anomaly   = AnomalyDetector(cfg, self.baseline)
        self.net_mon   = NetworkMonitor(cfg)
        self.vuln      = VulnScanner(cfg)
        self.auditor   = ConfigAuditor(cfg)
        self.recommender = Recommender()

        self._running = True

    # ── Signal handling ────────────────────────────────────────────────────────

    def handle_sigterm(self, *_):
        self.log.info("SIGTERM received — shutting down gracefully.")
        self._running = False

    def handle_sighup(self, *_):
        self.log.info("SIGHUP received — configuration reload not yet implemented; restart to apply changes.")

    # ── Single monitoring pass ─────────────────────────────────────────────────

    def run_once(self):
        self.log.info("=== Sentry monitoring cycle starting ===")
        findings = []

        # 1. Collect current metrics and update baseline
        try:
            metrics = self.baseline.collect()
            self.baseline.store(metrics)
            self.log.debug("Metrics collected: %d series", len(metrics))
        except Exception as exc:
            self.log.error("Baseline collection error: %s", exc)
            metrics = {}

        # 2. Anomaly detection
        try:
            anomalies = self.anomaly.detect(metrics)
            if anomalies:
                self.log.warning("Anomaly detector flagged %d metric(s).", len(anomalies))
                for a in anomalies:
                    rec = self.recommender.for_anomaly(a)
                    findings.append({
                        "source":      "anomaly_detector",
                        "severity":    a.get("severity", "warning"),
                        "title":       a.get("title", "Anomalous metric detected"),
                        "description": a.get("description", ""),
                        "recommendation": rec,
                        "detail":      a,
                    })
        except Exception as exc:
            self.log.error("Anomaly detection error: %s", exc)

        # 3. Network monitoring
        if self.cfg.getboolean("network_monitor", "enabled", fallback=True):
            try:
                net_issues = self.net_mon.check()
                for issue in net_issues:
                    rec = self.recommender.for_network(issue)
                    findings.append({
                        "source":      "network_monitor",
                        "severity":    issue.get("severity", "warning"),
                        "title":       issue.get("title", "Network anomaly"),
                        "description": issue.get("description", ""),
                        "recommendation": rec,
                        "detail":      issue,
                    })
            except Exception as exc:
                self.log.error("Network monitor error: %s", exc)

        # 4. Vulnerability scanning (scheduled independently)
        if self.cfg.getboolean("vuln_scanner", "enabled", fallback=True):
            try:
                vulns = self.vuln.scan_due()
                for v in vulns:
                    if v.get("vmid") in self.exclude_ids:
                        continue
                    rec = self.recommender.for_vulnerability(v)
                    findings.append({
                        "source":      "vuln_scanner",
                        "severity":    v.get("severity_level", "warning").lower(),
                        "title":       v.get("title", "Vulnerability detected"),
                        "description": v.get("description", ""),
                        "recommendation": rec,
                        "detail":      v,
                    })
            except Exception as exc:
                self.log.error("Vulnerability scanner error: %s", exc)

        # 5. Config audit
        if self.cfg.getboolean("config_auditor", "enabled", fallback=True):
            try:
                audit_issues = self.auditor.audit(exclude_ids=self.exclude_ids)
                for issue in audit_issues:
                    rec = self.recommender.for_config(issue)
                    findings.append({
                        "source":      "config_auditor",
                        "severity":    issue.get("severity", "warning"),
                        "title":       issue.get("title", "Configuration issue"),
                        "description": issue.get("description", ""),
                        "recommendation": rec,
                        "detail":      issue,
                    })
            except Exception as exc:
                self.log.error("Config auditor error: %s", exc)

        # 6. Dispatch alerts
        for finding in findings:
            self.log.info(
                "[%s] %s — %s",
                finding["severity"].upper(),
                finding["source"],
                finding["title"],
            )
            self.alertmgr.dispatch(finding)

        self.log.info(
            "=== Cycle complete — %d finding(s) ===",
            len(findings),
        )

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        interval = self.cfg.getint("sentry", "interval_seconds", fallback=300)
        self.log.info(
            "Proxmox Sentry v%s starting — interval=%ds", SENTRY_VERSION, interval
        )

        # Run immediately on start, then on schedule
        self.run_once()

        schedule.every(interval).seconds.do(self.run_once)

        while self._running:
            schedule.run_pending()
            time.sleep(min(interval, 10))

        self.log.info("Sentry agent stopped.")


# ── Entry point ────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Proxmox Sentry – AI-powered security monitoring agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config", "-c",
        default=os.environ.get("SENTRY_CONFIG", DEFAULT_CONFIG),
        help=f"Path to sentry.conf (default: {DEFAULT_CONFIG})",
    )
    p.add_argument(
        "--version", "-V",
        action="version",
        version=f"Proxmox Sentry v{SENTRY_VERSION}",
    )
    return p.parse_args()


def main():
    args = parse_args()

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    log_file = cfg.get("sentry", "log_file", fallback="/var/log/sentry/sentry.log")
    log      = setup_logging(log_file)

    agent = SentryAgent(cfg)
    signal.signal(signal.SIGTERM, agent.handle_sigterm)
    signal.signal(signal.SIGHUP, agent.handle_sighup)

    try:
        agent.run()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt — exiting.")


if __name__ == "__main__":
    main()
