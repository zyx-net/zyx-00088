from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import shutil

from ..config import Config
from ..models import BatchHistory, CorrectionAction, CorrectionType
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

    if not applied:
        raise ValueError("没有可撤销的操作")

    if correction_id:
        to_undo = [c for c in applied if c.id == correction_id]
        if not to_undo:
            raise ValueError(f"找不到已应用且未撤销的修正: {correction_id}")
    else:
        to_undo = list(reversed(applied))

    undone: List[CorrectionAction] = []
    failed: List[Dict] = []

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

        except Exception as e:
            failed.append({
                "correction_id": correction.id,
                "error": str(e),
            })

    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "undone_count": len(undone),
        "failed_count": len(failed),
        "undone": [c.to_dict() for c in undone],
        "failed": failed,
    }


def has_undoable_operations(batch: BatchHistory) -> bool:
    return any(c.applied and not c.rolled_back for c in batch.corrections)
