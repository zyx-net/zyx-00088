from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import shutil

from ..config import Config
from ..models import BatchHistory, CorrectionAction, CorrectionType
from ..scanner import FileScanner
from ..storage import BatchStorage
from .utils import get_or_create_batch


def apply_corrections(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
    correction_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict:
    from ..models import FileStatus

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

    scanner = FileScanner(config)

    applied: List[CorrectionAction] = []
    failed: List[Dict] = []
    hash_mismatches: List[Dict] = []

    for correction in pending:
        try:
            if correction.type in (CorrectionType.COPY, CorrectionType.MOVE):
                source = Path(correction.source)
                target = Path(correction.target)
                target.parent.mkdir(parents=True, exist_ok=True)

                if not source.exists():
                    raise FileNotFoundError(f"源文件不存在: {correction.source}")

                source_str = str(source.resolve())
                expected_hash = None
                if source_str in batch.scanned_files:
                    expected_hash = batch.scanned_files[source_str].hash

                current_hash = scanner.calculate_hash(source)

                if expected_hash and expected_hash != current_hash:
                    mismatch_info = {
                        "correction_id": correction.id,
                        "source_path": source_str,
                        "expected_hash": expected_hash,
                        "actual_hash": current_hash,
                        "target": str(target),
                    }
                    hash_mismatches.append(mismatch_info)
                    failed.append({
                        "correction_id": correction.id,
                        "error": f"哈希不一致: 扫描后文件已被篡改，源: {source_str}",
                    })

                    for item in batch.delivery_list.values():
                        if item.matched_source == source_str:
                            item.status = FileStatus.HASH_MISMATCH
                            item.actual_hash = current_hash
                    continue

                if correction.type == CorrectionType.COPY:
                    shutil.copy2(source, target)
                else:
                    shutil.move(source, target)

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
        "hash_mismatch_count": len(hash_mismatches),
        "applied": [c.to_dict() for c in applied],
        "failed": failed,
        "hash_mismatches": hash_mismatches,
    }


def apply_single_correction(
    config: Config,
    storage: BatchStorage,
    correction_id: str,
    batch_id: str = None,
) -> Dict:
    return apply_corrections(config, storage, batch_id, correction_id=correction_id)
