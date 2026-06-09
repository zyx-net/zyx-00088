from pathlib import Path
from typing import Optional, Tuple
import uuid

from ..config import Config
from ..models import BatchHistory, BatchNameConflictError
from ..storage import BatchStorage


def get_or_create_batch(
    storage: BatchStorage, batch_id: Optional[str] = None, batch_name: Optional[str] = None
) -> Tuple[BatchHistory, bool]:
    if batch_id:
        batch = storage.load(batch_id)
        if batch:
            if batch_name:
                if batch.name != batch_name:
                    _check_name_conflict(storage, batch_name, exclude_batch_id=batch_id)
                    batch.name = batch_name
                    batch.normalized_name = BatchHistory._normalize_name(batch_name)
            return batch, False
        raise ValueError(f"批次不存在: {batch_id}")

    if batch_name:
        _check_name_conflict(storage, batch_name)

        normalized = BatchHistory._normalize_name(batch_name)
        existing = storage.find_batches_by_normalized_name(normalized)
        if existing:
            match = next((b for b in existing if b.name == batch_name), None)
            if match:
                return match, False

    latest = storage.get_latest_batch()
    if latest and not batch_name:
        return latest, False

    new_id = str(uuid.uuid4())[:8]
    name = batch_name or f"batch-{new_id}"
    new_batch = BatchHistory(batch_id=new_id, name=name)
    storage.save(new_batch)
    return new_batch, True


def _check_name_conflict(storage: BatchStorage, batch_name: str, exclude_batch_id: Optional[str] = None) -> None:
    """检查批次名归一化冲突，冲突时抛出 BatchNameConflictError"""
    normalized = BatchHistory._normalize_name(batch_name)
    existing = storage.find_batches_by_normalized_name(normalized)

    if exclude_batch_id:
        existing = [b for b in existing if b.batch_id != exclude_batch_id]

    conflicting = [b for b in existing if b.name != batch_name]
    if conflicting:
        raise BatchNameConflictError(
            requested_name=batch_name,
            normalized_name=normalized,
            conflicting_batches=conflicting,
        )


def ensure_archive_dir(config: Config, batch_name: str) -> Path:
    archive_path = config.archive_dir / batch_name
    archive_path.mkdir(parents=True, exist_ok=True)
    return archive_path
