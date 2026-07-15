from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..config import AppConfig
from ..util.db import Database

logger = logging.getLogger("sophia.reconciliation")


def _summarize(result: Any) -> dict[str, Any]:
    """Reduce a worker result to a serializable, non-recursive summary."""
    if isinstance(result, dict):
        summary: dict[str, Any] = {}
        for key in ("ok", "processed", "succeeded", "failed", "attempted", "synced", "drift", "graph_only", "errors", "due", "pending_total", "healthy", "skipped", "reason", "error"):
            if key in result:
                summary[key] = result[key]
        return summary
    return {"value": str(result)}


class ReconciliationSupervisor:
    """Background loop that drives every recovery outbox in one place.

    Owns three workers that were previously only operator-triggered:
      * graph outbox replay (GraphOutbox -> Neo4j captures)
      * task outbox sweep (retry_failed_tasks -> AssistX)
      * voiceprint drift sync (reconcile_to_neo4j SQLite -> Neo4j)

    Exposes a unified status report that aggregates all outbox summaries into
    the same drift-report shape used elsewhere in the service.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        task_db: Database | None = None,
        graph_outbox: Any | None = None,
        registry: Any | None = None,
    ) -> None:
        self.config = config
        self.task_db = task_db
        self.graph_outbox = graph_outbox
        self.registry = registry
        self._task: asyncio.Task[None] | None = None
        self._stop = False
        self.last_runs: dict[str, dict[str, Any]] = {}

    @property
    def enabled(self) -> bool:
        return self.config.reconciliation.enabled

    def start(self) -> None:
        if not self.enabled:
            logger.info("reconciliation supervisor disabled by config")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop = False
        self._task = asyncio.create_task(self._loop())
        logger.info("reconciliation supervisor started (interval=%ss)", self.config.reconciliation.interval_seconds)

    def stop(self) -> None:
        self._stop = True
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        try:
            while not self._stop:
                try:
                    await self.run_once()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("reconciliation sweep failed: %s", exc)
                interval = max(1, self.config.reconciliation.interval_seconds)
                for _ in range(interval):
                    if self._stop:
                        break
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    async def run_once(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        results["graph_outbox"] = await asyncio.to_thread(self._sweep_graph_outbox)
        if self.config.reconciliation.task_outbox_sweep:
            results["tasks"] = await asyncio.to_thread(self._sweep_tasks)
        if self.config.reconciliation.voiceprint_drift_sync:
            results["voiceprints"] = await asyncio.to_thread(self._sweep_voiceprints)
        self.last_runs = {
            "ts_ms": int(time.time() * 1000),
            "workers": {k: _summarize(v) for k, v in results.items()},
        }
        return results

    def _sweep_graph_outbox(self) -> dict[str, Any]:
        if self.graph_outbox is None:
            return {"ok": True, "skipped": "no graph outbox configured"}
        cfg = self.config.neo4j
        if not cfg.password:
            return {"ok": False, "reason": "Neo4j password not configured"}
        from ..server.graph_outbox import replay_graph_outbox_items

        result = replay_graph_outbox_items(
            self.graph_outbox,
            neo4j_uri=cfg.uri,
            neo4j_user=cfg.user,
            neo4j_password=cfg.password,
            neo4j_database=cfg.database or None,
        )
        try:
            self.graph_outbox.prune_succeeded(older_than_ms=self.config.reconciliation.graph_outbox_prune_days * 24 * 60 * 60 * 1000)
        except Exception as exc:
            logger.warning("graph outbox prune failed: %s", exc)
        return result

    def _sweep_tasks(self) -> dict[str, Any]:
        if self.task_db is None:
            return {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": "no task db"}
        from ..server.assistant import retry_failed_tasks

        return retry_failed_tasks(self.task_db)

    def _sweep_voiceprints(self) -> dict[str, Any]:
        if self.registry is None:
            return {"skipped": "no voiceprint registry"}
        return self.registry.reconcile_to_neo4j(force=True, check_only=False)

    def status(self) -> dict[str, Any]:
        components: dict[str, Any] = {}
        if self.graph_outbox is not None:
            components["graph_outbox"] = self.graph_outbox.summary()
        if self.task_db is not None:
            components["tasks"] = self.task_db.task_summary()
        voiceprints = {}
        if self.registry is not None:
            try:
                voiceprints = self.registry.reconcile_to_neo4j(force=True, check_only=True)
            except Exception as exc:
                voiceprints = {"error": f"{type(exc).__name__}: {exc}"}
        components["voiceprints"] = voiceprints
        healthy = all(
            (c.get("healthy", True) if isinstance(c, dict) else True)
            for c in components.values()
        )
        return {
            "enabled": self.enabled,
            "last_run": self.last_runs or None,
            "components": components,
            "healthy": healthy,
        }
