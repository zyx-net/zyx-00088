from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import shutil
import uuid

from ..config import Config
from ..models import BatchHistory, CorrectionAction, CorrectionType, UndoRecord
from ..storage import BatchStorage
from .utils import get_or_create_batch


def undo_corrections(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
    correction_id: Optional[str] = None,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    applied = [c for c in batch.corrections if c.applied and not c.rolled_back]
    total_applied_before = len(applied)

    if not applied:
        raise ValueError("没有可撤销的操作")

    if correction_id:
        to_undo = [c for c in applied if c.id == correction_id]
        if not to_undo:
            already_undone = next((c for c in batch.corrections if c.id == correction_id and c.rolled_back), None)
            if already_undone:
                raise ValueError(f"修正 {correction_id} 已被撤销")
            else:
                raise ValueError(f"找不到已应用且未撤销的修正: {correction_id}")
    else:
        to_undo = list(reversed(applied))

    undone: List[CorrectionAction] = []
    failed: List[Dict] = []
    undone_ids: List[str] = []
    failed_ids: List[str] = []

    for correction in to_undo:
        try:
            if correction.type in (CorrectionType.COPY, CorrectionType.MOVE):
                target = Path(correction.target)
                if target.exists():
                    target.unlink()

            elif correction.type == CorrectionType.DELETE:
                target = Path(correction.target)
                if not target.exists():
                    source = Path(correction.source)
                    if source.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)

            correction.rolled_back = True
            correction.applied = False
            undone.append(correction)
            undone_ids.append(correction.id)

        except Exception as e:
            failed.append({
                "correction_id": correction.id,
                "error": str(e),
            })
            failed_ids.append(correction.id)

    remaining_applied = [c for c in batch.corrections if c.applied and not c.rolled_back]
    remaining_applied_after = len(remaining_applied)

    undo_record = UndoRecord(
        undo_id=str(uuid.uuid4())[:8],
        undone_at=datetime.now(),
        undone_count=len(undone),
        failed_count=len(failed),
        total_applied_before=total_applied_before,
        remaining_applied_after=remaining_applied_after,
        undone_ids=undone_ids,
        failed_ids=failed_ids,
        target_correction_id=correction_id,
    )

    batch.undo_records.append(undo_record)
    batch.last_undo_at = datetime.now()

    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "undo_id": undo_record.undo_id,
        "undone_count": len(undone),
        "failed_count": len(failed),
        "total_applied_before": total_applied_before,
        "remaining_applied_after": remaining_applied_after,
        "undone": [c.to_dict() for c in undone],
        "failed": failed,
        "undone_ids": undone_ids,
        "failed_ids": failed_ids,
        "target_correction_id": correction_id,
    }


def has_undoable_operations(batch: BatchHistory) -> bool:
    return any(c.applied and not c.rolled_back for c in batch.corrections)
