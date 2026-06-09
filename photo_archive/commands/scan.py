from datetime import datetime
from pathlib import Path
from typing import Dict, List

from ..config import Config
from ..models import BatchHistory, ScannedFile
from ..scanner import FileScanner
from ..storage import BatchStorage
from .utils import get_or_create_batch


def scan_directory(
    config: Config,
    storage: BatchStorage,
    source_dir: str,
    batch_id: str = None,
    batch_name: str = None,
    recursive: bool = True,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id, batch_name)

    scanner = FileScanner(config)
    scanned_files = scanner.scan_directory(source_dir, recursive=recursive)

    for sf in scanned_files:
        batch.scanned_files[sf.original_path] = sf

    batch.scan_source_dir = str(Path(source_dir).resolve())
    batch.last_scan_at = datetime.now()

    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "scanned_count": len(scanned_files),
        "source_dir": batch.scan_source_dir,
        "files": [sf.to_dict() for sf in scanned_files],
    }


def list_scanned_files(batch: BatchHistory) -> List[Dict]:
    return [sf.to_dict() for sf in batch.scanned_files.values()]
