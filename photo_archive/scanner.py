from pathlib import Path
from typing import List, Optional
import hashlib

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
