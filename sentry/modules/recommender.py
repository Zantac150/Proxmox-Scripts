"""
modules/recommender.py
Proxmox Sentry – recommendation engine.

Maps findings from other modules to human-readable, actionable
remediation guidance.  Recommendations are categorised into:
  - Immediate action   (critical severity)
  - Investigate        (warning severity)
  - Informational note (info severity)
"""

import logging
from typing import Any, Dict, Optional

log = logging.getLogger("sentry.recommender")

# ── Static recommendation library ─────────────────────────────────────────────

VULN_RECS = {
    "default": (
        "Update the affected package to the fixed version shown in the finding.  "
        "Run `apt-get update && apt-get upgrade` (Debian/Ubuntu) or the appropriate "
        "package manager for the container OS.  "
        "If no fix is yet available, consider isolating the affected container "
        "or applying a WAF/firewall rule to limit exposure."
    ),
}

CONFIG_RECS = {
    "privileged_container": (
        "Convert the container to unprivileged mode where possible.  "
        "In the Proxmox web UI, stop the container, enable 'Unprivileged Container', "
        "and restart.  If the workload requires specific kernel capabilities, "
        "grant only the minimum required via lxc.cap.keep instead of running privileged."
    ),
    "host_device_passthrough": (
        "Remove host device pass-through unless strictly necessary.  "
        "If a device must be shared, prefer using the Proxmox GPU/USB pass-through "
        "mechanisms with an unprivileged VM rather than an LXC bind-mount."
    ),
    "nesting_without_apparmor": (
        "Either disable nesting (features: nesting=0) or configure AppArmor "
        "to use the 'lxc-container-default-with-nesting' profile.  "
        "Running nested containers without AppArmor confinement increases risk."
    ),
    "no_firewall": (
        "Enable the Proxmox firewall for this guest and add explicit ACCEPT rules "
        "only for required services.  Use 'DROP' as the default INPUT policy.  "
        "Consider using the Proxmox Datacenter-level firewall for cluster-wide rules."
    ),
    "no_guest_agent": (
        "Install and enable the QEMU guest agent inside the VM "
        "(`apt-get install qemu-guest-agent` on Debian/Ubuntu, then "
        "`systemctl enable --now qemu-guest-agent`), and set `agent: 1` in the "
        "VM configuration."
    ),
    "low_security_score": (
        "Review the full list of configuration checks above and address each finding.  "
        "Start with critical issues (privileged mode, host devices) before warnings."
    ),
    "default": (
        "Review the flagged configuration item and apply the principle of least privilege.  "
        "Consult the Proxmox VE documentation and the CIS Benchmark for container hardening."
    ),
}

NETWORK_RECS = {
    "suspicious_port": (
        "Immediately investigate the process listening on this port.  "
        "Use `ss -tlnp` and `lsof -i :<port>` to identify the process.  "
        "If the process is unknown or unexpected, treat it as a potential compromise — "
        "isolate the system, capture memory if possible, and rebuild from a clean backup."
    ),
    "new_external_host": (
        "Verify that this external connection is expected for the workload running "
        "on this container/VM.  If unexpected, block the destination IP with a firewall "
        "rule and investigate whether the system has been compromised."
    ),
    "traffic_spike": (
        "Investigate the source of the traffic spike.  Check active connections with "
        "`ss -anp` and review application logs.  If the spike is unexplained, "
        "consider rate-limiting outbound traffic and checking for data exfiltration."
    ),
    "default": (
        "Review current network connections (`ss -anp`) and firewall rules.  "
        "Apply the principle of least network access — containers/VMs should only be "
        "able to reach services they explicitly require."
    ),
}

ANOMALY_RECS = {
    "ml_anomaly": (
        "An ML anomaly was detected in system metrics.  Compare the flagged metrics "
        "against expected baselines.  Check for runaway processes (`top`, `htop`), "
        "unexpected network activity, or scheduled jobs that could explain the spike.  "
        "If unexplained, treat as a potential security incident."
    ),
    "threshold": (
        "System resource utilisation has exceeded safe thresholds.  "
        "Identify the top resource consumers (`top`, `iotop`, `iftop`) and determine "
        "whether the load is legitimate.  Consider scaling up the host or migrating "
        "high-load guests."
    ),
    "default": (
        "Investigate the anomalous metric(s) listed in the finding.  "
        "Correlate with application and system logs around the time of detection."
    ),
}


class Recommender:
    """Map finding dicts to human-readable remediation recommendations."""

    def for_vulnerability(self, finding: Dict) -> str:
        return VULN_RECS.get("default", "")

    def for_config(self, finding: Dict) -> str:
        check = finding.get("check", "default")
        return CONFIG_RECS.get(check, CONFIG_RECS["default"])

    def for_network(self, finding: Dict) -> str:
        issue_type = finding.get("type", "default")
        return NETWORK_RECS.get(issue_type, NETWORK_RECS["default"])

    def for_anomaly(self, finding: Dict) -> str:
        issue_type = finding.get("type", "default")
        return ANOMALY_RECS.get(issue_type, ANOMALY_RECS["default"])

    def for_finding(self, finding: Dict) -> str:
        """Generic dispatcher — routes to the correct specialist method."""
        source = finding.get("source", "")
        if source == "vuln_scanner":
            return self.for_vulnerability(finding.get("detail", {}))
        if source == "config_auditor":
            return self.for_config(finding.get("detail", {}))
        if source == "network_monitor":
            return self.for_network(finding.get("detail", {}))
        if source == "anomaly_detector":
            return self.for_anomaly(finding.get("detail", {}))
        return "Review the finding and apply appropriate remediation."
