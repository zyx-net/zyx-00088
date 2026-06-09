from pathlib import Path
from typing import Dict, List, Optional
import json

from .models import BatchHistory


class BatchStorage:
    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.batches_dir = self.work_dir / "batches"
        self.batches_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.batches_dir / "index.json"

    def _get_batch_path(self, batch_id: str) -> Path:
        return self.batches_dir / f"{batch_id}.json"

    def save(self, batch: BatchHistory) -> None:
        from datetime import datetime
        batch.updated_at = datetime.now()
        batch_path = self._get_batch_path(batch.batch_id)
        with open(batch_path, "w", encoding="utf-8") as f:
            json.dump(batch.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(batch)

    def load(self, batch_id: str) -> Optional[BatchHistory]:
        batch_path = self._get_batch_path(batch_id)
        if not batch_path.exists():
            return None
        with open(batch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return BatchHistory.from_dict(data)

    def list_batches(self) -> List[Dict]:
        if not self.index_file.exists():
            return []
        with open(self.index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _update_index(self, batch: BatchHistory) -> None:
        batches = self.list_batches()
        existing = next((b for b in batches if b["batch_id"] == batch.batch_id), None)
        entry = {
            "batch_id": batch.batch_id,
            "name": batch.name,
            "created_at": batch.created_at.isoformat(),
            "updated_at": batch.updated_at.isoformat(),
            "file_count": len(batch.scanned_files),
            "delivery_count": len(batch.delivery_list),
            "correction_count": len(batch.corrections),
        }
        if existing:
            existing.update(entry)
        else:
            batches.append(entry)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(batches, f, ensure_ascii=False, indent=2)

    def delete(self, batch_id: str) -> bool:
        batch_path = self._get_batch_path(batch_id)
        if batch_path.exists():
            batch_path.unlink()
            batches = [b for b in self.list_batches() if b["batch_id"] != batch_id]
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(batches, f, ensure_ascii=False, indent=2)
            return True
        return False

    def get_latest_batch(self) -> Optional[BatchHistory]:
        batches = self.list_batches()
        if not batches:
            return None
        latest = max(batches, key=lambda b: b["updated_at"])
        return self.load(latest["batch_id"])
