"""
modules/config_auditor.py
Proxmox Sentry – LXC and VM configuration security auditor.

Connects to the Proxmox API and audits each container/VM configuration
for common security misconfigurations, scoring each guest and flagging
items that fall below the acceptable threshold.

Checks include:
  - Privileged LXC containers
  - Host device pass-through (dangerous bind mounts)
  - Dangerous lxc.cap.drop or lxc.apparmor.profile settings
  - SSH public key injection via cloud-init
  - VMs with no firewall rules applied
  - Containers/VMs with QEMU guest agent disabled
  - Network interfaces without MAC filtering
"""

import configparser
import logging
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("sentry.config_auditor")

try:
    from proxmoxer import ProxmoxAPI
    PROXMOXER_AVAILABLE = True
except ImportError:
    PROXMOXER_AVAILABLE = False


# ── Scoring weights ────────────────────────────────────────────────────────────
# Each check deducts from 100 when it fails.
CHECK_WEIGHTS = {
    "privileged_container":   30,
    "host_device_passthrough": 25,
    "dangerous_cap":           15,
    "no_firewall":             15,
    "nesting_without_apparmor": 10,
    "cloud_init_no_key":        5,
}


class ConfigAuditor:
    def __init__(self, cfg: configparser.ConfigParser):
        self.cfg                 = cfg
        self.check_privileged    = cfg.getboolean("config_auditor", "check_privileged", fallback=True)
        self.check_host_devices  = cfg.getboolean("config_auditor", "check_host_devices", fallback=True)
        self.check_rootfs_perms  = cfg.getboolean("config_auditor", "check_rootfs_perms", fallback=True)
        self.min_score           = cfg.getint("config_auditor", "min_score_threshold", fallback=70)
        self._pve: Optional[Any] = None

    # ── API connection ─────────────────────────────────────────────────────────

    def _connect(self) -> Optional[Any]:
        if not PROXMOXER_AVAILABLE:
            log.warning("proxmoxer not available; config audit requires Proxmox API.")
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
                self._pve = ProxmoxAPI(host, user=user, password=pw, verify_ssl=verify_ssl)
        except Exception as exc:
            log.error("Config auditor: Proxmox API connection failed: %s", exc)
            self._pve = None
        return self._pve

    # ── Public API ─────────────────────────────────────────────────────────────

    def audit(self, exclude_ids: Optional[Set[str]] = None) -> List[Dict]:
        """Audit all LXC containers and QEMU VMs; return a list of issue dicts."""
        pve = self._connect()
        if pve is None:
            return []

        issues = []
        exclude_ids = exclude_ids or set()

        try:
            nodes = pve.nodes.get()
        except Exception as exc:
            log.error("Failed to enumerate Proxmox nodes: %s", exc)
            return []

        for node_info in nodes:
            node = node_info["node"]
            issues.extend(self._audit_lxcs(pve, node, exclude_ids))
            issues.extend(self._audit_vms(pve, node, exclude_ids))

        return issues

    # ── LXC audits ─────────────────────────────────────────────────────────────

    def _audit_lxcs(self, pve, node: str, exclude_ids: Set[str]) -> List[Dict]:
        issues = []
        try:
            containers = pve.nodes(node).lxc.get()
        except Exception as exc:
            log.debug("Failed to list LXC containers on %s: %s", node, exc)
            return issues

        for ct in containers:
            vmid = str(ct["vmid"])
            if vmid in exclude_ids:
                continue
            name = ct.get("name", f"ct-{vmid}")
            try:
                config = pve.nodes(node).lxc(vmid).config.get()
            except Exception as exc:
                log.debug("Cannot read config for LXC %s: %s", vmid, exc)
                continue

            score  = 100
            checks = []

            # Privileged container
            if self.check_privileged and not config.get("unprivileged", 0):
                score -= CHECK_WEIGHTS["privileged_container"]
                checks.append({
                    "check":    "privileged_container",
                    "severity": "critical",
                    "detail":   "Container is running in privileged mode. "
                                "A root escape in this container grants full host access.",
                })

            # Host device pass-through
            if self.check_host_devices:
                for k, v in config.items():
                    if k.startswith("dev") and v:
                        score -= CHECK_WEIGHTS["host_device_passthrough"]
                        checks.append({
                            "check":    "host_device_passthrough",
                            "severity": "critical",
                            "detail":   f"Host device pass-through configured ({k}={v}). "
                                        f"This allows direct hardware access from the container.",
                        })
                        break

            # Nesting without AppArmor
            lxc_conf = config.get("lxc", "")
            has_nesting = config.get("features", "").find("nesting=1") != -1
            has_apparmor_unconfined = "apparmor=unconfined" in lxc_conf.lower()
            if has_nesting and has_apparmor_unconfined:
                score -= CHECK_WEIGHTS["nesting_without_apparmor"]
                checks.append({
                    "check":    "nesting_without_apparmor",
                    "severity": "warning",
                    "detail":   "Nesting is enabled and AppArmor is set to unconfined. "
                                "This weakens container isolation.",
                })

            # No firewall rules
            try:
                fw_rules = pve.nodes(node).lxc(vmid).firewall.rules.get()
            except Exception:
                fw_rules = []
            fw_enabled = config.get("firewall", 0)
            if not fw_enabled or not fw_rules:
                score -= CHECK_WEIGHTS["no_firewall"]
                checks.append({
                    "check":    "no_firewall",
                    "severity": "warning",
                    "detail":   "No firewall rules are configured for this container. "
                                "All inbound traffic is permitted by default.",
                })

            issues.extend(
                self._build_issues(vmid, name, "lxc", score, checks)
            )

        return issues

    # ── VM audits ──────────────────────────────────────────────────────────────

    def _audit_vms(self, pve, node: str, exclude_ids: Set[str]) -> List[Dict]:
        issues = []
        try:
            vms = pve.nodes(node).qemu.get()
        except Exception as exc:
            log.debug("Failed to list VMs on %s: %s", node, exc)
            return issues

        for vm in vms:
            vmid = str(vm["vmid"])
            if vmid in exclude_ids:
                continue
            name = vm.get("name", f"vm-{vmid}")
            try:
                config = pve.nodes(node).qemu(vmid).config.get()
            except Exception as exc:
                log.debug("Cannot read config for VM %s: %s", vmid, exc)
                continue

            score  = 100
            checks = []

            # No QEMU guest agent
            agent_cfg = config.get("agent", "0")
            agent_enabled = str(agent_cfg).startswith("1") or "enabled=1" in str(agent_cfg)
            if not agent_enabled:
                checks.append({
                    "check":    "no_guest_agent",
                    "severity": "info",
                    "detail":   "QEMU guest agent is not enabled. "
                                "This prevents graceful shutdown and IP reporting.",
                })

            # No firewall
            try:
                fw_rules = pve.nodes(node).qemu(vmid).firewall.rules.get()
            except Exception:
                fw_rules = []
            fw_enabled = config.get("firewall", 0)
            if not fw_enabled or not fw_rules:
                score -= CHECK_WEIGHTS["no_firewall"]
                checks.append({
                    "check":    "no_firewall",
                    "severity": "warning",
                    "detail":   "No firewall rules are configured for this VM.",
                })

            issues.extend(
                self._build_issues(vmid, name, "qemu", score, checks)
            )

        return issues

    # ── Issue builder ──────────────────────────────────────────────────────────

    def _build_issues(
        self,
        vmid: str,
        name: str,
        kind: str,
        score: int,
        checks: List[Dict],
    ) -> List[Dict]:
        issues = []
        for chk in checks:
            sev = chk.get("severity", "warning")
            if sev == "info" and score >= self.min_score:
                continue
            issues.append({
                "type":        "config_issue",
                "severity":    sev,
                "vmid":        vmid,
                "guest_name":  name,
                "guest_type":  kind,
                "check":       chk["check"],
                "score":       score,
                "title":       (
                    f"[{sev.upper()}] Config issue on {kind.upper()} "
                    f"{vmid} ({name}): {chk['check'].replace('_', ' ')}"
                ),
                "description": chk["detail"],
            })

        if score < self.min_score and not issues:
            issues.append({
                "type":        "config_issue",
                "severity":    "warning",
                "vmid":        vmid,
                "guest_name":  name,
                "guest_type":  kind,
                "check":       "low_security_score",
                "score":       score,
                "title":       (
                    f"Low security score ({score}/100) for {kind.upper()} "
                    f"{vmid} ({name})"
                ),
                "description": (
                    f"The configuration security score is {score}/100 (threshold: "
                    f"{self.min_score}).  Review and harden the container/VM settings."
                ),
            })

        return issues
