"""
modules/anomaly_detector.py
Proxmox Sentry – ML anomaly detection using scikit-learn.

Trains an Isolation Forest on historical baseline metrics and scores
each incoming metric snapshot, flagging outliers as anomalies.
"""

import configparser
import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np

try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

log = logging.getLogger("sentry.anomaly_detector")

# Minimum samples before the model is trained
MIN_TRAINING_SAMPLES = 50
# Model is retrained every N seconds
MODEL_TTL_SECONDS = 3600


class AnomalyDetector:
    """
    Scores a metrics snapshot using a trained Isolation Forest.
    Falls back to threshold-based static rules when insufficient history
    exists or scikit-learn is unavailable.
    """

    def __init__(self, cfg: configparser.ConfigParser, baseline):
        self.cfg          = cfg
        self.baseline     = baseline
        self.contamination = cfg.getfloat("sentry", "anomaly_threshold", fallback=0.10)
        self._model_keys: List[str] = []
        self._model_ts    = 0.0
        self._pipeline: Optional[Any] = None

    # ── Model lifecycle ────────────────────────────────────────────────────────

    def _needs_retrain(self) -> bool:
        return (time.time() - self._model_ts) > MODEL_TTL_SECONDS

    def _train(self):
        """Fit (or re-fit) the Isolation Forest on historical data."""
        if not SKLEARN_AVAILABLE:
            log.warning("scikit-learn not available; skipping ML model training.")
            return

        metric_keys = self.baseline.get_all_metric_keys()
        if not metric_keys:
            log.debug("No metric history yet; skipping model training.")
            return

        X = self.baseline.build_feature_matrix(metric_keys)
        if X is None or X.shape[0] < MIN_TRAINING_SAMPLES:
            log.debug(
                "Insufficient history for training (%d samples); need %d.",
                X.shape[0] if X is not None else 0,
                MIN_TRAINING_SAMPLES,
            )
            return

        log.info("Training Isolation Forest on %d samples × %d features…", *X.shape)

        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("iso_forest", IsolationForest(
                contamination=self.contamination,
                n_estimators=200,
                random_state=42,
                n_jobs=-1,
            )),
        ])
        pipeline.fit(X)

        self._pipeline   = pipeline
        self._model_keys = metric_keys
        self._model_ts   = time.time()

        # Persist for reload across restarts
        self.baseline.save_model("isolation_forest", {
            "pipeline": pipeline,
            "keys":     metric_keys,
            "trained_at": self._model_ts,
        })
        log.info("Isolation Forest trained and saved.")

    def _load_cached_model(self):
        """Load a previously persisted model if available and fresh enough."""
        obj = self.baseline.load_model("isolation_forest")
        if obj is None:
            return
        age = time.time() - obj.get("trained_at", 0)
        if age > MODEL_TTL_SECONDS * 4:
            log.debug("Cached model is stale (%ds old); will retrain.", int(age))
            return
        self._pipeline   = obj["pipeline"]
        self._model_keys = obj["keys"]
        self._model_ts   = obj["trained_at"]
        log.info("Loaded cached Isolation Forest (age %ds).", int(age))

    # ── Detection ──────────────────────────────────────────────────────────────

    def detect(self, metrics: Dict[str, float]) -> List[Dict]:
        """
        Score the metrics snapshot.  Returns a list of anomaly dicts, one per
        flagged issue.  An empty list means no anomalies.
        """
        anomalies = []

        # Always run static threshold checks first (work without history)
        anomalies.extend(self._static_checks(metrics))

        if not SKLEARN_AVAILABLE:
            return anomalies

        # Lazy model load / retrain
        if self._pipeline is None:
            self._load_cached_model()
        if self._needs_retrain():
            self._train()
        if self._pipeline is None:
            return anomalies

        # Build feature vector aligned to model keys
        x = np.array(
            [metrics.get(k, 0.0) for k in self._model_keys],
            dtype=float,
        ).reshape(1, -1)

        try:
            score  = self._pipeline.score_samples(x)[0]     # negative = more anomalous
            pred   = self._pipeline.predict(x)[0]            # -1 = anomaly, 1 = normal
        except Exception as exc:
            log.error("Model scoring error: %s", exc)
            return anomalies

        if pred == -1:
            # Identify the most anomalous individual feature
            feature_devs = self._feature_contributions(x)
            top_features = sorted(feature_devs.items(), key=lambda kv: kv[1], reverse=True)[:5]

            severity = "critical" if score < -0.4 else "warning"
            top_desc = ", ".join(
                f"{k}={metrics.get(k, 0):.2f}" for k, _ in top_features
            )
            anomalies.append({
                "type":        "ml_anomaly",
                "severity":    severity,
                "title":       "ML model detected abnormal system behaviour",
                "description": (
                    f"Isolation Forest anomaly score {score:.3f}. "
                    f"Top contributing metrics: {top_desc}."
                ),
                "score":       score,
                "top_features": top_features,
            })

        return anomalies

    def _feature_contributions(self, x: np.ndarray) -> Dict[str, float]:
        """Estimate per-feature deviation from training mean (as proxy for contribution)."""
        if not SKLEARN_AVAILABLE or self._pipeline is None:
            return {}
        try:
            scaler: StandardScaler = self._pipeline.named_steps["scaler"]
            x_scaled = scaler.transform(x)[0]
            return {k: abs(float(v)) for k, v in zip(self._model_keys, x_scaled)}
        except Exception:
            return {}

    # ── Static threshold checks ────────────────────────────────────────────────

    @staticmethod
    def _static_checks(metrics: Dict[str, float]) -> List[Dict]:
        """Rule-based checks that work without ML history."""
        issues = []

        cpu = metrics.get("pve.cpu_pct", metrics.get("host.load1", -1))
        if 0 <= cpu > 95:
            issues.append({
                "type":        "threshold",
                "severity":    "critical",
                "title":       f"CPU usage critical ({cpu:.1f}%)",
                "description": "Node CPU utilisation exceeded 95%.",
            })
        elif 0 <= cpu > 85:
            issues.append({
                "type":        "threshold",
                "severity":    "warning",
                "title":       f"CPU usage high ({cpu:.1f}%)",
                "description": "Node CPU utilisation exceeded 85%.",
            })

        mem = metrics.get("pve.mem_used_pct", metrics.get("host.mem_used_pct", -1))
        if 0 <= mem > 95:
            issues.append({
                "type":        "threshold",
                "severity":    "critical",
                "title":       f"Memory usage critical ({mem:.1f}%)",
                "description": "Node memory utilisation exceeded 95%.",
            })
        elif 0 <= mem > 90:
            issues.append({
                "type":        "threshold",
                "severity":    "warning",
                "title":       f"Memory usage high ({mem:.1f}%)",
                "description": "Node memory utilisation exceeded 90%.",
            })

        return issues
