from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    threshold: float = 0.75
    require_challenge: bool = False
    challenge_phrases_file: Optional[str] = None


class STTConfig(BaseModel):
    sample_rate: int = 16000
    chunk_ms: int = 30
    decode_interval_ms: int = 500
    window_ms: int = 5000
    overlap_ms: int = 1000
    refine_enabled: bool = True


class VADConfig(BaseModel):
    aggressiveness: int = 2
    energy_threshold: float = 0.01
    min_speech_ms: int = 300
    max_silence_ms: int = 500


class LLMConfig(BaseModel):
    provider: str = "mock"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: str = "local-model"
    max_steps: int = 4


class TTSConfig(BaseModel):
    backend: str = "fallback"
    voice: Optional[str] = None


class PathsConfig(BaseModel):
    artifacts_dir: str = "runs"
    workspace_dir: str = "workspace"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765


class AppConfig(BaseModel):
    auth: AuthConfig = Field(default_factory=AuthConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


def load_config(path: Optional[str]) -> AppConfig:
    if not path:
        return AppConfig()
    data = yaml.safe_load(Path(path).read_text())
    return AppConfig.model_validate(data or {})
