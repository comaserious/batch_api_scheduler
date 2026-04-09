from dataclasses import dataclass
from typing import Optional
import yaml


@dataclass
class ServiceConfig:
    callback_url: str
    default_model: str
    default_type: str


class ServiceRegistry:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self._services: dict[str, ServiceConfig] = {
            name: ServiceConfig(
                callback_url=cfg["callback_url"],
                default_model=cfg["default_model"],
                default_type=cfg["default_type"],
            )
            for name, cfg in raw["services"].items()
        }

    def get(self, service_name: str) -> Optional[ServiceConfig]:
        return self._services.get(service_name)

    def exists(self, service_name: str) -> bool:
        return service_name in self._services
