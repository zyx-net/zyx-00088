from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import shutil

from ..config import Config
from ..models import BatchHistory, CorrectionAction, CorrectionType
from ..storage import BatchStorage
from .utils import get_or_create_batch


def apply_corrections(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
    correction_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    if not batch.corrections:
        raise ValueError("没有可应用的修正计划，请先生成修正计划")

    pending = [c for c in batch.corrections if not c.applied and not c.rolled_back]

    if correction_id:
        pending = [c for c in pending if c.id == correction_id]
        if not pending:
            raise ValueError(f"找不到未应用的修正: {correction_id}")

    if limit and limit > 0:
        pending = pending[:limit]

    applied: List[CorrectionAction] = []
    failed: List[Dict] = []

    for correction in pending:
        try:
            if correction.type in (CorrectionType.COPY, CorrectionType.MOVE):
                source = Path(correction.source)
                target = Path(correction.target)
                target.parent.mkdir(parents=True, exist_ok=True)

                if correction.type == CorrectionType.COPY:
                    if source.exists():
                        shutil.copy2(source, target)
                    else:
                        raise FileNotFoundError(f"源文件不存在: {correction.source}")
                else:
                    if source.exists():
                        shutil.move(source, target)
                    else:
                        raise FileNotFoundError(f"源文件不存在: {correction.source}")

            elif correction.type == CorrectionType.DELETE:
                target = Path(correction.target)
                if target.exists():
                    target.unlink()

            correction.applied = True
            correction.applied_at = datetime.now()
            applied.append(correction)

        except Exception as e:
            failed.append({
                "correction_id": correction.id,
                "error": str(e),
            })

    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "applied_count": len(applied),
        "failed_count": len(failed),
        "applied": [c.to_dict() for c in applied],
        "failed": failed,
    }


def apply_single_correction(
    config: Config,
    storage: BatchStorage,
    correction_id: str,
    batch_id: str = None,
) -> Dict:
    return apply_corrections(config, storage, batch_id, correction_id=correction_id)
