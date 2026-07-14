from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    threshold: float = 0.75
    require_challenge: bool = False
    challenge_phrases_file: Optional[str] = None
    owner_user_id: str = "scott"
    owner_override_enabled: bool = False
    owner_override_token: Optional[str] = None
    owner_override_token_file: Optional[str] = None
    owner_append_min_seconds: float = 2.0
    owner_append_max_seconds: float = 30.0
    global_speaker_link_enabled: bool = True
    global_speaker_link_threshold: float = 0.85


class STTConfig(BaseModel):
    sample_rate: int = 16000
    chunk_ms: int = 30
    decode_interval_ms: int = 500
    window_ms: int = 5000
    overlap_ms: int = 1000
    refine_enabled: bool = True
    model_size: str = "tiny"
    compute_type: str = "int8"
    cpu_threads: int = 2


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
    intent_provider: str = "mock"
    intent_base_url: Optional[str] = None
    intent_api_key: Optional[str] = None
    intent_model: str = "draft-model"
    max_steps: int = 4
    system_prompt: str = (
        "You are Sophia voice overlay for Hermes. Keep responses concise, actionable, and safe."
    )


class TTSConfig(BaseModel):
    backend: str = "fallback"
    voice: Optional[str] = None
    openvoice_model_path: Optional[str] = None
    coqui_model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    speaker_wav: Optional[str] = None
    use_gpu: bool = False


class PathsConfig(BaseModel):
    artifacts_dir: str = "runs"
    workspace_dir: str = "workspace"
    capture_dir: Optional[str] = None


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765
    protocol: str = "native_ws"


class RuntimeConfig(BaseModel):
    max_memory_gb: float = 8.0
    allow_hf_downloads: bool = False


class Neo4jConfig(BaseModel):
    uri: str = "bolt://host.docker.internal:7687"
    user: str = "neo4j"
    password: Optional[str] = None
    password_file: Optional[str] = None
    database: str = "memory"
    default_speaker_name: Optional[str] = None


class AppConfig(BaseModel):
    auth: AuthConfig = Field(default_factory=AuthConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)


def load_config(path: Optional[str]) -> AppConfig:
    if not path:
        return _apply_env(AppConfig())
    data = yaml.safe_load(Path(path).read_text())
    return _apply_env(AppConfig.model_validate(data or {}))


def _read_secret_file(path: str | None) -> str | None:
    if not path:
        return None
    secret_path = Path(path).expanduser()
    if not secret_path.exists():
        return None
    value = secret_path.read_text(encoding="utf-8").strip()
    return value or None


def _apply_env(config: AppConfig) -> AppConfig:
    update = {}
    if os.getenv("NEO4J_URI"):
        update["uri"] = os.environ["NEO4J_URI"]
    if os.getenv("NEO4J_USER"):
        update["user"] = os.environ["NEO4J_USER"]
    if os.getenv("NEO4J_PASSWORD"):
        update["password"] = os.environ["NEO4J_PASSWORD"]
    if os.getenv("NEO4J_PASSWORD_FILE"):
        update["password_file"] = os.environ["NEO4J_PASSWORD_FILE"]
    if os.getenv("NEO4J_DATABASE"):
        update["database"] = os.environ["NEO4J_DATABASE"]
    if os.getenv("NEO4J_DEFAULT_SPEAKER"):
        update["default_speaker_name"] = os.environ["NEO4J_DEFAULT_SPEAKER"]
    if update:
        config.neo4j = config.neo4j.model_copy(update=update)
    if config.neo4j.password is None and config.neo4j.password_file:
        token = _read_secret_file(config.neo4j.password_file)
        if token:
            config.neo4j = config.neo4j.model_copy(update={"password": token})
    if os.getenv("SOPHIA_CAPTURE_DIR"):
        config.paths = config.paths.model_copy(update={"capture_dir": os.environ["SOPHIA_CAPTURE_DIR"]})
    llm_update = {}
    if os.getenv("SOPHIA_INTENT_PROVIDER"):
        llm_update["intent_provider"] = os.environ["SOPHIA_INTENT_PROVIDER"]
    if os.getenv("SOPHIA_INTENT_BASE_URL"):
        llm_update["intent_base_url"] = os.environ["SOPHIA_INTENT_BASE_URL"]
    if os.getenv("SOPHIA_INTENT_API_KEY"):
        llm_update["intent_api_key"] = os.environ["SOPHIA_INTENT_API_KEY"]
    if os.getenv("SOPHIA_INTENT_MODEL"):
        llm_update["intent_model"] = os.environ["SOPHIA_INTENT_MODEL"]
    if llm_update:
        config.llm = config.llm.model_copy(update=llm_update)

    auth_update = {}
    if os.getenv("SOPHIA_OWNER_USER_ID"):
        auth_update["owner_user_id"] = os.environ["SOPHIA_OWNER_USER_ID"]
    if os.getenv("SOPHIA_OWNER_OVERRIDE_ENABLED"):
        auth_update["owner_override_enabled"] = os.environ["SOPHIA_OWNER_OVERRIDE_ENABLED"].lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if os.getenv("SOPHIA_OWNER_OVERRIDE_TOKEN"):
        auth_update["owner_override_token"] = os.environ["SOPHIA_OWNER_OVERRIDE_TOKEN"]
    if os.getenv("SOPHIA_OWNER_OVERRIDE_TOKEN_FILE"):
        auth_update["owner_override_token_file"] = os.environ["SOPHIA_OWNER_OVERRIDE_TOKEN_FILE"]
    if os.getenv("SOPHIA_OWNER_APPEND_MIN_SECONDS"):
        auth_update["owner_append_min_seconds"] = float(os.environ["SOPHIA_OWNER_APPEND_MIN_SECONDS"])
    if os.getenv("SOPHIA_OWNER_APPEND_MAX_SECONDS"):
        auth_update["owner_append_max_seconds"] = float(os.environ["SOPHIA_OWNER_APPEND_MAX_SECONDS"])
    if os.getenv("SOPHIA_GLOBAL_SPEAKER_LINK_ENABLED"):
        auth_update["global_speaker_link_enabled"] = os.environ["SOPHIA_GLOBAL_SPEAKER_LINK_ENABLED"].lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if os.getenv("SOPHIA_GLOBAL_SPEAKER_LINK_THRESHOLD"):
        auth_update["global_speaker_link_threshold"] = float(os.environ["SOPHIA_GLOBAL_SPEAKER_LINK_THRESHOLD"])
    if auth_update:
        config.auth = config.auth.model_copy(update=auth_update)
    if config.auth.owner_override_token is None and config.auth.owner_override_token_file:
        token = _read_secret_file(config.auth.owner_override_token_file)
        if token:
            config.auth = config.auth.model_copy(update={"owner_override_token": token})
    return config
