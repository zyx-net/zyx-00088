from dataclasses import dataclass, field
from pathlib import Path
from typing import List
import yaml


@dataclass
class Config:
    naming_rule: str = "{机位}_{批次}_{序号:04d}.{扩展名}"
    cameras: List[str] = field(default_factory=lambda: ["A", "B", "C"])
    hash_strategy: str = "sha256"
    archive_dir: Path = Path("./archive")
    work_dir: Path = Path("./work")
    config_path: Path = Path("./config.yaml")

    @classmethod
    def load(cls, config_path: str = "./config.yaml") -> "Config":
        path = Path(config_path)
        if not path.exists():
            return cls(config_path=path)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        default = cls()
        return cls(
            naming_rule=data.get("naming_rule", default.naming_rule),
            cameras=data.get("cameras", default.cameras),
            hash_strategy=data.get("hash_strategy", default.hash_strategy),
            archive_dir=Path(data.get("archive_dir", default.archive_dir)),
            work_dir=Path(data.get("work_dir", default.work_dir)),
            config_path=path,
        )

    def save(self) -> None:
        data = {
            "naming_rule": self.naming_rule,
            "cameras": self.cameras,
            "hash_strategy": self.hash_strategy,
            "archive_dir": str(self.archive_dir),
            "work_dir": str(self.work_dir),
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
