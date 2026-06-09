from pathlib import Path
from typing import Optional, Tuple
import uuid

from ..config import Config
from ..models import BatchHistory
from ..storage import BatchStorage


def get_or_create_batch(
    storage: BatchStorage, batch_id: Optional[str] = None, batch_name: Optional[str] = None
) -> Tuple[BatchHistory, bool]:
    if batch_id:
        batch = storage.load(batch_id)
        if batch:
            return batch, False
        raise ValueError(f"批次不存在: {batch_id}")

    batch = storage.get_latest_batch()
    if batch:
        return batch, False

    new_id = str(uuid.uuid4())[:8]
    name = batch_name or f"batch-{new_id}"
    new_batch = BatchHistory(batch_id=new_id, name=name)
    storage.save(new_batch)
    return new_batch, True


def ensure_archive_dir(config: Config, batch_name: str) -> Path:
    archive_path = config.archive_dir / batch_name
    archive_path.mkdir(parents=True, exist_ok=True)
    return archive_path
