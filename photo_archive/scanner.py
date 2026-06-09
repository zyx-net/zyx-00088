from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib
import re

from .config import Config
from .models import ScannedFile


class FileScanner:
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".raw", ".cr2", ".nef", ".arw", ".dng"}

    def __init__(self, config: Config):
        self.config = config
        self.hash_strategy = config.hash_strategy.lower()

    def calculate_hash(self, file_path: Path) -> str:
        if self.hash_strategy == "md5":
            hasher = hashlib.md5()
        elif self.hash_strategy == "sha1":
            hasher = hashlib.sha1()
        elif self.hash_strategy == "sha512":
            hasher = hashlib.sha512()
        else:
            hasher = hashlib.sha256()

        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def detect_camera(self, file_name: str) -> Optional[str]:
        upper_name = file_name.upper()
        for cam in self.config.cameras:
            if f"_{cam}_" in upper_name or upper_name.startswith(f"{cam}_") or upper_name.endswith(f"_{cam}"):
                return cam
        for cam in self.config.cameras:
            if upper_name.startswith(cam):
                return cam
        return None

    def detect_batch(self, file_name: str) -> Optional[str]:
        parts = file_name.replace(".", "_").split("_")
        for part in parts:
            if part and not part.isdigit() and len(part) > 2:
                return part
        return None

    def scan_directory(self, directory: str, recursive: bool = True) -> List[ScannedFile]:
        dir_path = Path(directory)
        if not dir_path.exists():
            raise FileNotFoundError(f"目录不存在: {directory}")
        if not dir_path.is_dir():
            raise NotADirectoryError(f"不是目录: {directory}")

        scanned_files: List[ScannedFile] = []
        pattern = "**/*" if recursive else "*"

        for file_path in dir_path.glob(pattern):
            if file_path.is_file() and file_path.suffix.lower() in self.IMAGE_EXTENSIONS:
                try:
                    file_stat = file_path.stat()
                    file_hash = self.calculate_hash(file_path)
                    camera = self.detect_camera(file_path.name)
                    batch = self.detect_batch(file_path.name)

                    scanned = ScannedFile(
                        original_path=str(file_path.resolve()),
                        file_name=file_path.name,
                        size=file_stat.st_size,
                        hash=file_hash,
                        extension=file_path.suffix.lower().lstrip("."),
                        camera=camera,
                        detected_batch=batch,
                    )
                    scanned_files.append(scanned)
                except Exception as e:
                    print(f"警告: 无法处理文件 {file_path}: {e}")

        return scanned_files

    def format_target_name(self, camera: str, batch_name: str, sequence: int, extension: str) -> str:
        return self.config.naming_rule.format(
            机位=camera,
            批次=batch_name,
            序号=sequence,
            扩展名=extension,
        )

    def _build_naming_pattern(self) -> Tuple[re.Pattern, List[str]]:
        rule = self.config.naming_rule
        fields = []
        pattern = rule

        pattern = pattern.replace(".", r"\.")

        def replace_field(match):
            field_expr = match.group(1)
            parts = field_expr.split(":")
            field_name = parts[0]
            fields.append(field_name)
            if field_name == "序号":
                return r"(\d+)"
            elif field_name == "机位":
                return r"([A-Za-z0-9]+)"
            elif field_name == "批次":
                return r"([A-Za-z0-9_]+)"
            elif field_name == "扩展名":
                return r"([a-z0-9]+)"
            else:
                return r"(.+?)"

        pattern = re.sub(r"\{([^}]+)\}", replace_field, pattern)
        return re.compile(f"^{pattern}$", re.IGNORECASE), fields

    def parse_filename(self, file_name: str) -> Optional[Dict[str, str]]:
        pattern, fields = self._build_naming_pattern()
        match = pattern.match(file_name)
        if not match:
            return None

        result = {}
        for i, field in enumerate(fields):
            result[field] = match.group(i + 1)

        if "序号" in result:
            try:
                result["序号"] = int(result["序号"])
            except (ValueError, TypeError):
                pass

        return result

    def extract_sequence(self, file_name: str) -> Optional[int]:
        parsed = self.parse_filename(file_name)
        if parsed and "序号" in parsed:
            seq = parsed["序号"]
            return int(seq) if isinstance(seq, str) else seq

        numbers = re.findall(r"\d+", file_name.replace(".", "_"))
        if numbers:
            return int(numbers[-1])
        return None

    def extract_fields(self, file_name: str) -> Dict[str, Optional[str]]:
        result = {
            "机位": self.detect_camera(file_name),
            "批次": self.detect_batch(file_name),
            "序号": None,
            "扩展名": Path(file_name).suffix.lower().lstrip("."),
        }

        parsed = self.parse_filename(file_name)
        if parsed:
            for key, value in parsed.items():
                if key in result and value is not None:
                    result[key] = str(value) if not isinstance(value, int) else value

        if result["序号"] is None:
            result["序号"] = self.extract_sequence(file_name)

        return result
