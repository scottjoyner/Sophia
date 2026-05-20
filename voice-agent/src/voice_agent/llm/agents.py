from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class Agent:
    name: str
    role: str
    handler: Callable[[str], str]


class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent:
        return self._agents[name]

    def all(self) -> Dict[str, Agent]:
        return self._agents
