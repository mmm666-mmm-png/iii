"""
基础设施 —— 配置管理

统一管理所有外部服务的配置项，从环境变量读取，提供默认值。
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATHS = (
    _PROJECT_ROOT / ".env",
    _PROJECT_ROOT / ".env.local",
    _PROJECT_ROOT / "server" / ".env",
    _PROJECT_ROOT / "server" / ".env.local",
    _PROJECT_ROOT / "python_worker" / ".env",
    _PROJECT_ROOT / "python_worker" / ".env.local",
)


def _load_env_file(path: Path) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except Exception:
        pass

    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                value = value.strip().strip('"').strip("'")
                if " #" in value:
                    value = value.split(" #", 1)[0].rstrip()
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


for _env_path in _ENV_PATHS:
    if _env_path.exists():
        _load_env_file(_env_path)


@dataclass
class AmapConfig:
    """高德地图API配置。"""
    api_key: str = ""
    base_url: str = "https://restapi.amap.com/v3"
    walking_path_url: str = "https://restapi.amap.com/v3/direction/walking"
    timeout_seconds: int = 10
    city: str = "枣庄"

    @classmethod
    def from_env(cls) -> "AmapConfig":
        return cls(
            api_key=os.getenv("AMAP_API_KEY", ""),
            base_url=os.getenv("AMAP_BASE_URL", "https://restapi.amap.com/v3"),
            timeout_seconds=int(os.getenv("AMAP_TIMEOUT", "10")),
            city=os.getenv("AMAP_CITY", "枣庄"),
        )


@dataclass
class QwenConfig:
    """通义千问大模型配置。"""
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-turbo"
    voice_model: str = "qwen-omni-turbo"
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "QwenConfig":
        return cls(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=os.getenv("QWEN_MODEL", "qwen-turbo"),
            voice_model=os.getenv("QWEN_VOICE_MODEL", "qwen-omni-turbo"),
            timeout_seconds=int(os.getenv("QWEN_TIMEOUT", "30")),
        )


@dataclass
class TactilePavingConfig:
    """盲道数据配置。"""
    geojson_path: str = ""
    default_city: str = "枣庄"

    @classmethod
    def from_env(cls) -> "TactilePavingConfig":
        return cls(
            geojson_path=os.getenv("TACTILE_PAVING_GEOJSON", ""),
            default_city=os.getenv("TACTILE_PAVING_CITY", "枣庄"),
        )


@dataclass
class AppConfig:
    """应用总配置。"""
    amap: AmapConfig = field(default_factory=AmapConfig.from_env)
    qwen: QwenConfig = field(default_factory=QwenConfig.from_env)
    tactile: TactilePavingConfig = field(default_factory=TactilePavingConfig.from_env)
    host: str = "0.0.0.0"
    port: int = 18082

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            host=os.getenv("PYTHON_WORKER_HOST", "0.0.0.0"),
            port=int(os.getenv("PYTHON_WORKER_PORT", "18082")),
        )
