from __future__ import annotations

from dataclasses import dataclass
from typing import List

from ..config import AppConfig
from .agents import Agent, AgentRegistry
from .openai_compat_provider import OpenAICompatProvider
from .provider_base import LLMProvider, MockProvider
from .tools import ToolExecutor


@dataclass
class RalphStep:
    step: int
    plan: str
    result: str
    done: bool


class RalphLoop:
    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = self._build_provider()
        self.tools = ToolExecutor(config.paths.workspace_dir)
        self.agents = self._build_agents()

    def _build_provider(self) -> LLMProvider:
        if self.config.llm.provider == "openai" and self.config.llm.base_url:
            return OpenAICompatProvider(self.config.llm.base_url, self.config.llm.api_key, self.config.llm.model)
        return MockProvider()

    def _build_agents(self) -> AgentRegistry:
        registry = AgentRegistry()
        registry.register(Agent(name="planner", role="planner", handler=self._agent_complete))
        registry.register(Agent(name="executor", role="executor", handler=self._agent_complete))
        registry.register(Agent(name="researcher", role="researcher", handler=self._agent_complete))
        return registry

    def _agent_complete(self, prompt: str) -> str:
        return self.provider.complete(prompt).content

    def run(self, transcript: str) -> str:
        steps: List[RalphStep] = []
        workspace_state = ""
        for idx in range(self.config.llm.max_steps):
            prompt = (
                f"You are a planner. Transcript: {transcript}. "
                f"Workspace: {workspace_state}. Provide next action and say DONE if finished."
            )
            plan = self.agents.get("planner").handler(prompt)
            done = "DONE" in plan.upper()
            result = ""
            if not done:
                exec_prompt = f"Execute task: {plan}."
                result = self.agents.get("executor").handler(exec_prompt)
            steps.append(RalphStep(step=idx + 1, plan=plan, result=result, done=done))
            workspace_state += f"\nStep {idx + 1} plan: {plan}\nResult: {result}"
            if done:
                break
        if steps:
            final = steps[-1].result or steps[-1].plan
        else:
            final = ""
        return final
