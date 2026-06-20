#!/usr/bin/env python3
"""
Proxmox Autonomous Fabric (PAF)
================================

Experimental orchestration framework that combines:
1) Intent-based operations
2) Time-travel environment replay
3) Predictive failure genome scoring
4) Live economic scheduler
5) Blast-radius containment
6) Self-authoring runbooks
7) Cross-layer digital twin simulation
8) Human trust layer explanations

The script is intentionally self-contained and offline-capable.
It consumes JSON snapshots/intents/policies and emits an execution plan.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


@dataclass
class PlannedAction:
    action_id: str
    phase: str
    title: str
    target: str
    reason: str
    confidence: float
    rollback: str
    expected_gain: str
    risk: str
    blast_radius_score: float
    containment_zone: str


class IntentPlanner:
    def __init__(self, intent: Dict[str, Any]):
        self.intent = intent

    def build_goals(self) -> List[Dict[str, Any]]:
        goals = self.intent.get("goals", [])
        if goals:
            return goals

        free_text = self.intent.get("text", "").lower()
        inferred = []
        if "cost" in free_text or "energy" in free_text:
            inferred.append({"name": "minimize_cost", "priority": "high"})
        if "ha" in free_text or "availability" in free_text:
            inferred.append({"name": "maximize_availability", "priority": "critical"})
        if "security" in free_text:
            inferred.append({"name": "reduce_exposure", "priority": "high"})
        return inferred


class TimeTravelReplay:
    def __init__(self, snapshot_store: Path):
        self.snapshot_store = snapshot_store

    def append_snapshot(self, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"captured_at": utc_now(), "state": state}
        records = self._read_all()
        records.append(payload)
        write_json(self.snapshot_store, {"snapshots": records})
        return payload

    def get_snapshot(self, at_timestamp: Optional[str]) -> Dict[str, Any]:
        records = self._read_all()
        if not records:
            raise ValueError("No snapshots available in snapshot store.")
        if not at_timestamp:
            return records[-1]

        target = datetime.fromisoformat(at_timestamp.replace("Z", "+00:00"))
        selected = records[0]
        for snap in records:
            ts = datetime.fromisoformat(snap["captured_at"].replace("Z", "+00:00"))
            if ts <= target:
                selected = snap
            else:
                break
        return selected

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self.snapshot_store.exists():
            return []
        payload = load_json(self.snapshot_store)
        return payload.get("snapshots", [])


class FailureGenome:
    def score_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        hist = node.get("history", {})
        cpu = hist.get("cpu", [])
        temp = hist.get("temp_c", [])
        io_wait = hist.get("io_wait", [])
        mem = hist.get("memory_pct", [])

        def drift(series: List[float]) -> float:
            if len(series) < 2:
                return 0.0
            return max(0.0, series[-1] - statistics.mean(series[:-1]))

        score = (
            drift(cpu) * 0.25
            + drift(temp) * 0.30
            + drift(io_wait) * 0.25
            + drift(mem) * 0.20
        )

        level = "low"
        if score >= 18:
            level = "critical"
        elif score >= 10:
            level = "high"
        elif score >= 5:
            level = "medium"

        return {
            "node": node.get("name", "unknown"),
            "genome_score": round(score, 2),
            "risk_level": level,
            "recommendation": "pre-evacuate non-critical workloads" if level in {"high", "critical"} else "observe",
        }


class EconomicScheduler:
    def placement_score(self, node: Dict[str, Any], vm: Dict[str, Any], tariffs: Dict[str, Any]) -> float:
        energy_price = tariffs.get(node.get("energy_zone", "default"), tariffs.get("default", 0.18))
        thermal_penalty = max(0.0, float(node.get("thermal_index", 0.0)))
        free_cpu = max(1.0, 100.0 - float(node.get("cpu_pct", 0.0)))
        free_mem = max(1.0, 100.0 - float(node.get("memory_pct", 0.0)))

        sla_weight = 2.0 if vm.get("sla", "standard") == "critical" else 1.0
        return (free_cpu * 0.35 + free_mem * 0.35) / (energy_price * sla_weight + 1 + thermal_penalty)

    def recommend_host(self, vm: Dict[str, Any], nodes: List[Dict[str, Any]], tariffs: Dict[str, Any]) -> Tuple[str, float]:
        ranked = sorted(
            ((n.get("name", "unknown"), self.placement_score(n, vm, tariffs)) for n in nodes),
            key=lambda x: x[1],
            reverse=True,
        )
        if not ranked:
            return "unknown", 0.0
        return ranked[0]


class BlastRadiusModel:
    def score(self, action: Dict[str, Any], state: Dict[str, Any]) -> Tuple[float, str]:
        vm_map = {vm.get("id"): vm for vm in state.get("workloads", [])}
        target_vm = vm_map.get(action.get("vm_id"), {})

        deps = target_vm.get("dependencies", [])
        criticality = target_vm.get("criticality", "standard")
        cluster_load = statistics.mean([n.get("cpu_pct", 0) for n in state.get("nodes", [])] or [0])

        score = min(100.0, len(deps) * 12 + (35 if criticality == "critical" else 15) + cluster_load * 0.25)

        zone = target_vm.get("zone", "zone-a")
        return round(score, 2), zone


class DigitalTwin:
    def simulate(self, actions: List[PlannedAction], state: Dict[str, Any]) -> Dict[str, Any]:
        perf_gain = 0.0
        risk_delta = 0.0
        for action in actions:
            if "migrate" in action.title.lower():
                perf_gain += 1.8
                risk_delta -= 1.2
            if "throttle" in action.title.lower():
                perf_gain -= 0.6
                risk_delta -= 2.3
            if "patch" in action.title.lower():
                risk_delta -= 1.8

        return {
            "predicted_performance_delta_pct": round(perf_gain, 2),
            "predicted_risk_delta_pct": round(risk_delta, 2),
            "simulated_at": utc_now(),
            "input_workloads": len(state.get("workloads", [])),
        }


class TrustLayer:
    def explain(self, action: PlannedAction) -> Dict[str, Any]:
        return {
            "why_now": action.reason,
            "expected_gain": action.expected_gain,
            "risk": action.risk,
            "fallback": action.rollback,
            "confidence": action.confidence,
            "sre_summary": (
                f"{action.title} on {action.target} within {action.containment_zone}; "
                f"blast-radius={action.blast_radius_score}/100."
            ),
        }


class RunbookAuthor:
    def append_event(self, history_file: Path, event: Dict[str, Any]) -> None:
        data = {"events": []}
        if history_file.exists():
            data = load_json(history_file)
            data.setdefault("events", [])
        data["events"].append(event)
        write_json(history_file, data)

    def generate(self, history_file: Path) -> str:
        data = load_json(history_file) if history_file.exists() else {"events": []}
        lines = ["# Proxmox Autonomous Fabric Runbook", ""]
        for idx, event in enumerate(data.get("events", []), start=1):
            lines.extend(
                [
                    f"## Event {idx}: {event.get('timestamp', 'unknown time')}",
                    f"- Intent: {event.get('intent', 'n/a')}",
                    f"- Decision count: {event.get('decision_count', 0)}",
                    f"- Twin risk delta: {event.get('twin_risk_delta_pct', 0)}%",
                    f"- Notes: {event.get('notes', 'none')}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"


class PolicyGate:
    def __init__(self, policy: Dict[str, Any]):
        self.policy = policy

    def allow(self, action: PlannedAction) -> bool:
        max_blast = float(self.policy.get("max_blast_radius", 75))
        min_conf = float(self.policy.get("min_confidence", 0.55))
        blocked = set(self.policy.get("blocked_actions", []))
        if action.title in blocked:
            return False
        return action.blast_radius_score <= max_blast and action.confidence >= min_conf


def create_actions(intent_goals: List[Dict[str, Any]], state: Dict[str, Any], policy: Dict[str, Any]) -> List[PlannedAction]:
    genome = FailureGenome()
    scheduler = EconomicScheduler()
    blast = BlastRadiusModel()

    tariffs = policy.get("tariffs", {"default": 0.18})
    nodes = state.get("nodes", [])
    workloads = state.get("workloads", [])

    node_risk = {x["node"]: x for x in (genome.score_node(n) for n in nodes)}
    actions: List[PlannedAction] = []

    action_num = 1
    for goal in intent_goals:
        name = goal.get("name", "")
        if name == "minimize_cost":
            for vm in workloads:
                host, score = scheduler.recommend_host(vm, nodes, tariffs)
                action = {
                    "vm_id": vm.get("id"),
                    "title": f"Migrate {vm.get('name', vm.get('id'))} to {host}",
                }
                blast_score, zone = blast.score(action, state)
                actions.append(
                    PlannedAction(
                        action_id=f"A{action_num:03d}",
                        phase="economics",
                        title=action["title"],
                        target=f"vm/{vm.get('id')}",
                        reason="Lower-cost node placement based on tariff and thermal profile.",
                        confidence=round(min(0.95, 0.55 + score / 100), 2),
                        rollback=f"live-migrate vm/{vm.get('id')} back to previous host",
                        expected_gain="reduce projected energy burn for this workload",
                        risk="temporary migration latency",
                        blast_radius_score=blast_score,
                        containment_zone=zone,
                    )
                )
                action_num += 1

        elif name == "maximize_availability":
            for node_name, risk in node_risk.items():
                if risk["risk_level"] in {"high", "critical"}:
                    fake_action = {"vm_id": workloads[0].get("id") if workloads else None, "title": f"Pre-evacuate workloads from {node_name}"}
                    blast_score, zone = blast.score(fake_action, state)
                    actions.append(
                        PlannedAction(
                            action_id=f"A{action_num:03d}",
                            phase="resilience",
                            title=f"Pre-evacuate workloads from {node_name}",
                            target=f"node/{node_name}",
                            reason=f"Failure genome risk is {risk['risk_level']} ({risk['genome_score']}).",
                            confidence=0.82 if risk["risk_level"] == "critical" else 0.68,
                            rollback=f"restore previous placement manifest for node/{node_name}",
                            expected_gain="avoid compound node failure impact",
                            risk="resource contention on destination nodes",
                            blast_radius_score=blast_score,
                            containment_zone=zone,
                        )
                    )
                    action_num += 1

        elif name == "reduce_exposure":
            for vm in workloads:
                fake_action = {"vm_id": vm.get("id"), "title": f"Patch and harden {vm.get('name', vm.get('id'))}"}
                blast_score, zone = blast.score(fake_action, state)
                actions.append(
                    PlannedAction(
                        action_id=f"A{action_num:03d}",
                        phase="security",
                        title=f"Patch and harden {vm.get('name', vm.get('id'))}",
                        target=f"vm/{vm.get('id')}",
                        reason="Reduce attack surface from drifted package and config state.",
                        confidence=0.71,
                        rollback=f"snapshot rollback for vm/{vm.get('id')}",
                        expected_gain="lower vulnerability and misconfiguration risk",
                        risk="service restart during patch window",
                        blast_radius_score=blast_score,
                        containment_zone=zone,
                    )
                )
                action_num += 1

    gate = PolicyGate(policy)
    return [a for a in actions if gate.allow(a)]


def orchestrate(args: argparse.Namespace) -> int:
    intent = load_json(Path(args.intent_file))
    policy = load_json(Path(args.policy_file))
    state = load_json(Path(args.state_file))

    replay = TimeTravelReplay(Path(args.snapshot_store))
    if args.capture_snapshot:
        replay.append_snapshot(state)

    if args.replay_at:
        state = replay.get_snapshot(args.replay_at)["state"]

    planner = IntentPlanner(intent)
    goals = planner.build_goals()
    actions = create_actions(goals, state, policy)

    twin = DigitalTwin().simulate(actions, state)
    trust = TrustLayer()

    payload = {
        "generated_at": utc_now(),
        "intent": intent,
        "goals": goals,
        "actions": [
            {
                **asdict(a),
                "trust": trust.explain(a),
            }
            for a in actions
        ],
        "digital_twin": twin,
        "policy_applied": policy,
    }

    if args.output_json:
        write_json(Path(args.output_json), payload)
    else:
        print(json.dumps(payload, indent=2))

    if args.history_file:
        RunbookAuthor().append_event(
            Path(args.history_file),
            {
                "timestamp": payload["generated_at"],
                "intent": intent.get("text", "; ".join(g.get("name", "") for g in goals)),
                "decision_count": len(actions),
                "twin_risk_delta_pct": twin["predicted_risk_delta_pct"],
                "notes": "autogenerated by PAF orchestrate",
            },
        )

    return 0


def capture_snapshot(args: argparse.Namespace) -> int:
    state = load_json(Path(args.state_file))
    payload = TimeTravelReplay(Path(args.snapshot_store)).append_snapshot(state)
    print(json.dumps(payload, indent=2))
    return 0


def build_runbook(args: argparse.Namespace) -> int:
    md = RunbookAuthor().generate(Path(args.history_file))
    if args.output_markdown:
        out = Path(args.output_markdown)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
    else:
        print(md, end="")
    return 0


def parser_builder() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Proxmox Autonomous Fabric (PAF) orchestration utility")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_orch = sub.add_parser("orchestrate", help="Generate autonomous execution plan")
    p_orch.add_argument("--intent-file", required=True)
    p_orch.add_argument("--state-file", required=True)
    p_orch.add_argument("--policy-file", required=True)
    p_orch.add_argument("--snapshot-store", default="autonomous-fabric/state-snapshots.json")
    p_orch.add_argument("--capture-snapshot", action="store_true")
    p_orch.add_argument("--replay-at", help="ISO timestamp to replay snapshot state from")
    p_orch.add_argument("--history-file", help="JSON execution history for runbook authoring")
    p_orch.add_argument("--output-json", help="Write plan payload JSON")
    p_orch.set_defaults(func=orchestrate)

    p_snap = sub.add_parser("capture-snapshot", help="Store cluster state snapshot")
    p_snap.add_argument("--state-file", required=True)
    p_snap.add_argument("--snapshot-store", default="autonomous-fabric/state-snapshots.json")
    p_snap.set_defaults(func=capture_snapshot)

    p_runbook = sub.add_parser("runbook", help="Generate markdown runbook from execution history")
    p_runbook.add_argument("--history-file", required=True)
    p_runbook.add_argument("--output-markdown", help="Path to write runbook markdown")
    p_runbook.set_defaults(func=build_runbook)

    return p


def main() -> int:
    args = parser_builder().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
