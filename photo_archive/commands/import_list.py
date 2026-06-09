from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
import csv

from ..config import Config
from ..models import BatchHistory, DeliveryItem, FileStatus
from ..storage import BatchStorage
from .utils import get_or_create_batch


def import_delivery_list(
    config: Config,
    storage: BatchStorage,
    manifest_path: str,
    batch_id: str = None,
    batch_name: str = None,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id, batch_name)

    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"清单文件不存在: {manifest_path}")

    items: List[DeliveryItem] = []
    has_corrupted = False

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                target_name = row.get("target_name") or row.get("filename") or row.get("file")
                if not target_name:
                    has_corrupted = True
                    continue

                item = DeliveryItem(
                    target_name=target_name.strip(),
                    expected_hash=row.get("expected_hash") or row.get("hash") or None,
                    expected_camera=row.get("camera") or row.get("expected_camera") or None,
                    expected_size=int(row["size"]) if row.get("size") else None,
                    sequence=int(row["sequence"]) if row.get("sequence") else None,
                )
                items.append(item)
    except Exception as e:
        raise ValueError(f"清单文件格式损坏: {e}")

    if has_corrupted:
        raise ValueError("清单文件包含损坏的条目")

    duplicate_names = set()
    seen_names = set()
    for item in items:
        if item.target_name in seen_names:
            duplicate_names.add(item.target_name)
        seen_names.add(item.target_name)

    if duplicate_names:
        batch.last_import_at = datetime.now()
        storage.save(batch)
        raise ValueError(f"清单中存在重复文件名: {', '.join(sorted(duplicate_names))}")

    batch.delivery_list.clear()
    for item in items:
        batch.delivery_list[item.target_name] = item

    batch.last_import_at = datetime.now()
    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "imported_count": len(items),
        "source_file": str(path.resolve()),
        "items": [item.to_dict() for item in items],
    }
