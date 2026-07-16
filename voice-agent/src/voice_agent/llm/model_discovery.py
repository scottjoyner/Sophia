from __future__ import annotations

# DEPRECATED (LLD §3.1 W-15): Sophia's local fleet/model selection duplicates
# the auto-router/auto-assign responsibilities. It is DISABLED by default
# (gated behind SOPHIA_LOCAL_FLEET_DISCOVERY / config.local_fleet_discovery) and
# Sophia now delegates routing to the auto-router. Kept for transitional use;
# do not extend — route new workloads through auto-router/auto-assign.
import logging
import re
import threading
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(b|m)", re.I)


def _params_of(model: str) -> float:
    """Approximate model size in billions of params from its name."""
    best = 0.0
    for num, unit in _PARAM_RE.findall(model):
        v = float(num) * (0.001 if unit.lower() == "m" else 1.0)
        if v > best:
            best = v
    return best or 3.0


@dataclass
class ModelEndpoint:
    node: str
    model_id: str
    base_url: str
    params: float
    is_embedding: bool = False

    @property
    def full_id(self) -> str:
        return f"lmstudio-{self.node}.{self.model_id}"


class ModelDiscoverer:
    """Discover lmstudio OpenAI-compatible endpoints across the Tailscale fleet.

    Each Tailscale node that runs lmstudio exposes its models at
    ``http://<node>:1234/v1/models`` (reachable via MagicDNS). This discoverer
    probes every candidate node directly (bypassing the central auto-router,
    which does not faithfully route to specific lmstudio models) and builds a
    catalog the assistant can route chat vs. task workloads across.

    Node names are learned from a configured candidate list and/or the
    auto-router's ``/v1/models`` ``lmstudio-<node>.<model>`` ids, then validated
    by probing each node's ``:1234`` endpoint.
    """

    # Known nodes; discovery also learns more from the router's model list.
    DEFAULT_NODES = [
        "x1-370",
        "destroyer",
        "beelink-ryzen-7-mini-pc",
        "optiplex-9030-aio",
        "lenovo-ideapad-330s-15ikb",
        "scotts-macbook-air",
    ]

    def __init__(
        self,
        *,
        node_port: int = 1234,
        router_models_url: str | None = None,
        candidate_nodes: list[str] | None = None,
        refresh_interval: float = 30.0,
        chat_max_params: float = 4.0,
        task_min_params: float = 20.0,
        probe_timeout: float = 4.0,
    ):
        self.node_port = node_port
        self.router_models_url = router_models_url or "http://host.docker.internal:8088/v1/models"
        self.candidate_nodes = list(candidate_nodes or [])
        self.refresh_interval = refresh_interval
        self.chat_max_params = chat_max_params
        self.task_min_params = task_min_params
        self.probe_timeout = probe_timeout
        self._lock = threading.Lock()
        self._endpoints: list[ModelEndpoint] = []
        self._last_used: dict[str, float] = {}  # full_id -> last selected ts
        self._last_refresh = 0.0
        self._thread: threading.Thread | None = None
        self._started = False
        self._router_warned = False

    def _mark_used(self, ep: ModelEndpoint) -> None:
        self._last_used[ep.full_id] = time.time()

    def _idle_score(self, ep: ModelEndpoint, now: float) -> float:
        """Higher = more idle. Nodes used longest ago earn a positive pull so
        under-utilised machines are dragged back into the rotation (worker-bee
        style load spreading) instead of one node being hammered."""
        last = self._last_used.get(ep.full_id, 0.0)
        return now - last  # seconds since last use; bigger = idler

    # -- discovery ---------------------------------------------------------
    def _discover_nodes(self) -> list[str]:
        nodes = set(self.candidate_nodes)
        nodes.update(self.DEFAULT_NODES)
        try:
            data = requests.get(self.router_models_url, timeout=5).json()
            for entry in data.get("data", []):
                mid = entry.get("id", "")
                if mid.startswith("lmstudio-"):
                    node = mid[len("lmstudio-"):].partition(".")[0]
                    if node:
                        nodes.add(node)
        except Exception as exc:  # router optional; never block discovery
            if not self._router_warned:
                logger.warning("fleet: auto-router model list unavailable: %s", exc)
                self._router_warned = True
            else:
                logger.debug("fleet: auto-router model list unavailable: %s", exc)
        return sorted(nodes)

    def _probe_node(self, node: str) -> list[ModelEndpoint]:
        url = f"http://{node}:{self.node_port}/v1/models"
        try:
            data = requests.get(url, timeout=self.probe_timeout).json()
        except Exception:
            return []
        out: list[ModelEndpoint] = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            if not mid or "embed" in mid.lower():
                continue  # skip embedding models for chat/task routing
            out.append(
                ModelEndpoint(
                    node=node,
                    model_id=mid,
                    base_url=f"http://{node}:{self.node_port}",
                    params=_params_of(mid),
                    is_embedding=False,
                )
            )
        return out

    def discover(self, force: bool = False) -> list[ModelEndpoint]:
        now = time.time()
        if not force and self._endpoints and now - self._last_refresh < self.refresh_interval:
            return self._endpoints
        nodes = self._discover_nodes()
        found: list[ModelEndpoint] = []
        for node in nodes:
            found.extend(self._probe_node(node))
        with self._lock:
            self._endpoints = found
            self._last_refresh = now
        logger.info("fleet: discovered %d model endpoints across %d nodes", len(found), len({e.node for e in found}))
        return found

    def _loop(self) -> None:
        while True:
            try:
                self.discover(force=True)
            except Exception:
                pass
            time.sleep(self.refresh_interval)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            self.discover(force=True)
        except Exception:
            pass
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # -- access ------------------------------------------------------------
    def endpoints(self) -> list[ModelEndpoint]:
        with self._lock:
            return list(self._endpoints)

    def _non_embedding(self) -> list[ModelEndpoint]:
        return [e for e in self.endpoints() if not e.is_embedding]

    def chat_endpoint(self) -> ModelEndpoint | None:
        """Fastest model for interactive chat, preferring idle (low-utilisation)
        machines so the fleet is used like worker bees.

        Selection: among chat-capable models (params <= chat_max_params, or all
        if none qualify), prefer the most-idle node (longest since last use) and
        break ties toward the smaller/faster model."""
        eps = self._non_embedding()
        if not eps:
            return None
        now = time.time()
        candidates = [e for e in eps if e.params <= self.chat_max_params] or eps
        candidates.sort(key=lambda e: (self._idle_score(e, now), -e.params), reverse=True)
        chosen = candidates[0]
        self._mark_used(chosen)
        return chosen

    def task_endpoint(self) -> ModelEndpoint | None:
        """Most capable model for heavy lifting (task extraction, reasoning),
        preferring idle nodes among the largest models."""
        eps = self._non_embedding()
        if not eps:
            return None
        now = time.time()
        candidates = [e for e in eps if e.params >= self.task_min_params] or eps
        candidates.sort(key=lambda e: (e.params, self._idle_score(e, now)), reverse=True)
        chosen = candidates[0]
        self._mark_used(chosen)
        return chosen

    def snapshot(self) -> dict[str, object]:
        eps = self.endpoints()
        ce = self.chat_endpoint()
        te = self.task_endpoint()
        return {
            "node_port": self.node_port,
            "nodes_seen": sorted({e.node for e in eps}),
            "endpoint_count": len(eps),
            "chat_endpoint": ce.full_id if ce else None,
            "task_endpoint": te.full_id if te else None,
            "endpoints": [
                {"node": e.node, "model_id": e.model_id, "params": e.params, "base_url": e.base_url}
                for e in sorted(eps, key=lambda x: (x.node, -x.params))
            ],
        }
