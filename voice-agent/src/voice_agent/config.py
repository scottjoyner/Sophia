from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    threshold: float = 0.75
    require_challenge: bool = False
    challenge_phrases_file: str | None = None
    owner_user_id: str = "scott"
    owner_override_enabled: bool = False
    owner_override_token: str | None = None
    owner_override_token_file: str | None = None
    owner_append_min_seconds: float = 2.0
    owner_append_max_seconds: float = 30.0
    global_speaker_link_enabled: bool = True
    global_speaker_link_threshold: float = 0.85
    adaptive_threshold_enabled: bool = True
    adaptive_threshold_min: float = 0.6
    adaptive_threshold_max: float = 1.0
    adaptive_threshold_alpha: float = 0.1
    adaptive_threshold_margin: float = 0.05


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
    base_url: str | None = None
    api_key: str | None = None
    model: str = "local-model"
    intent_provider: str = "mock"
    intent_base_url: str | None = None
    intent_api_key: str | None = None
    intent_model: str = "draft-model"
    task_model: str | None = None
    timeout: float = 60.0
    task_timeout: float | None = None
    task_extract_timeout: float | None = None
    fleet_discovery: bool = False
    fleet_node_port: int = 1234
    fleet_router_url: str | None = None
    fleet_candidate_nodes: str | None = None
    fleet_refresh_interval: float = 30.0
    fleet_chat_max_params: float = 4.0
    fleet_task_min_params: float = 20.0
    max_steps: int = 4
    system_prompt: str = (
        "You are Sophia voice overlay for Hermes. Keep responses concise, actionable, and safe."
    )


class TTSConfig(BaseModel):
    backend: str = "fallback"
    voice: str | None = None
    openvoice_model_path: str | None = None
    coqui_model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2"
    speaker_wav: str | None = None
    use_gpu: bool = False


class PathsConfig(BaseModel):
    artifacts_dir: str = "runs"
    workspace_dir: str = "workspace"
    capture_dir: str | None = None


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
    password: str | None = None
    password_file: str | None = None
    database: str = "memory"
    default_speaker_name: str | None = None


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


def load_config(path: str | None) -> AppConfig:
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
    if os.getenv("SOPHIA_LLM_PROVIDER"):
        llm_update["provider"] = os.environ["SOPHIA_LLM_PROVIDER"]
    if os.getenv("SOPHIA_LLM_BASE_URL"):
        llm_update["base_url"] = os.environ["SOPHIA_LLM_BASE_URL"]
    if os.getenv("SOPHIA_LLM_API_KEY"):
        llm_update["api_key"] = os.environ["SOPHIA_LLM_API_KEY"]
    if os.getenv("SOPHIA_LLM_MODEL"):
        llm_update["model"] = os.environ["SOPHIA_LLM_MODEL"]
    if os.getenv("SOPHIA_INTENT_PROVIDER"):
        llm_update["intent_provider"] = os.environ["SOPHIA_INTENT_PROVIDER"]
    if os.getenv("SOPHIA_INTENT_BASE_URL"):
        llm_update["intent_base_url"] = os.environ["SOPHIA_INTENT_BASE_URL"]
    if os.getenv("SOPHIA_INTENT_API_KEY"):
        llm_update["intent_api_key"] = os.environ["SOPHIA_INTENT_API_KEY"]
    if os.getenv("SOPHIA_INTENT_MODEL"):
        llm_update["intent_model"] = os.environ["SOPHIA_INTENT_MODEL"]
    if os.getenv("SOPHIA_TASK_MODEL"):
        llm_update["task_model"] = os.environ["SOPHIA_TASK_MODEL"]
    if os.getenv("SOPHIA_LLM_TIMEOUT"):
        llm_update["timeout"] = float(os.environ["SOPHIA_LLM_TIMEOUT"])
    if os.getenv("SOPHIA_TASK_TIMEOUT"):
        llm_update["task_timeout"] = float(os.environ["SOPHIA_TASK_TIMEOUT"])
    if os.getenv("SOPHIA_TASK_EXTRACT_TIMEOUT"):
        llm_update["task_extract_timeout"] = float(os.environ["SOPHIA_TASK_EXTRACT_TIMEOUT"])
    if os.getenv("SOPHIA_FLEET_DISCOVERY"):
        llm_update["fleet_discovery"] = os.environ["SOPHIA_FLEET_DISCOVERY"].lower() in {"1", "true", "yes", "on"}
    if os.getenv("SOPHIA_FLEET_NODE_PORT"):
        llm_update["fleet_node_port"] = int(os.environ["SOPHIA_FLEET_NODE_PORT"])
    if os.getenv("SOPHIA_FLEET_ROUTER_URL"):
        llm_update["fleet_router_url"] = os.environ["SOPHIA_FLEET_ROUTER_URL"]
    if os.getenv("SOPHIA_FLEET_CANDIDATE_NODES"):
        llm_update["fleet_candidate_nodes"] = os.environ["SOPHIA_FLEET_CANDIDATE_NODES"]
    if os.getenv("SOPHIA_FLEET_REFRESH_INTERVAL"):
        llm_update["fleet_refresh_interval"] = float(os.environ["SOPHIA_FLEET_REFRESH_INTERVAL"])
    if os.getenv("SOPHIA_FLEET_CHAT_MAX_PARAMS"):
        llm_update["fleet_chat_max_params"] = float(os.environ["SOPHIA_FLEET_CHAT_MAX_PARAMS"])
    if os.getenv("SOPHIA_FLEET_TASK_MIN_PARAMS"):
        llm_update["fleet_task_min_params"] = float(os.environ["SOPHIA_FLEET_TASK_MIN_PARAMS"])
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
    if os.getenv("SOPHIA_ADAPTIVE_THRESHOLD_ENABLED"):
        auth_update["adaptive_threshold_enabled"] = os.environ["SOPHIA_ADAPTIVE_THRESHOLD_ENABLED"].lower() in {
            "1", "true", "yes", "on",
        }
    if os.getenv("SOPHIA_ADAPTIVE_THRESHOLD_MIN"):
        auth_update["adaptive_threshold_min"] = float(os.environ["SOPHIA_ADAPTIVE_THRESHOLD_MIN"])
    if os.getenv("SOPHIA_ADAPTIVE_THRESHOLD_MAX"):
        auth_update["adaptive_threshold_max"] = float(os.environ["SOPHIA_ADAPTIVE_THRESHOLD_MAX"])
    if os.getenv("SOPHIA_ADAPTIVE_THRESHOLD_ALPHA"):
        auth_update["adaptive_threshold_alpha"] = float(os.environ["SOPHIA_ADAPTIVE_THRESHOLD_ALPHA"])
    if os.getenv("SOPHIA_ADAPTIVE_THRESHOLD_MARGIN"):
        auth_update["adaptive_threshold_margin"] = float(os.environ["SOPHIA_ADAPTIVE_THRESHOLD_MARGIN"])
    if auth_update:
        config.auth = config.auth.model_copy(update=auth_update)
    if config.auth.owner_override_token is None and config.auth.owner_override_token_file:
        token = _read_secret_file(config.auth.owner_override_token_file)
        if token:
            config.auth = config.auth.model_copy(update={"owner_override_token": token})
    return config
