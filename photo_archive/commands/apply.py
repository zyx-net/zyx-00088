from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import shutil
import uuid

from ..config import Config
from ..models import ApplyRecord, BatchHistory, CorrectionAction, CorrectionType
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

    all_corrections = batch.corrections
    total_count = len(all_corrections)

    pending = [c for c in all_corrections if not c.applied and not c.rolled_back]
    applied_before = [c for c in all_corrections if c.applied and not c.rolled_back]
    skipped = []

    if correction_id:
        target_correction = next((c for c in pending if c.id == correction_id), None)
        if not target_correction:
            already_applied = next((c for c in all_corrections if c.id == correction_id), None)
            if already_applied:
                if already_applied.applied and not already_applied.rolled_back:
                    skipped.append(already_applied)
                    pending = []
                elif already_applied.rolled_back:
                    raise ValueError(f"修正 {correction_id} 已被撤销，如需重新应用请先生成新计划")
            else:
                raise ValueError(f"找不到修正: {correction_id}")
        else:
            pending = [target_correction]

    if limit and limit > 0:
        pending = pending[:limit]

    scanner = FileScanner(config)

    applied: List[CorrectionAction] = []
    failed: List[Dict] = []
    hash_mismatches: List[Dict] = []
    applied_ids: List[str] = []
    failed_ids: List[str] = []
    skipped_ids = [c.id for c in skipped]

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
                    failed_ids.append(correction.id)

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

            elif correction.type == CorrectionType.SKIP:
                pass

            correction.applied = True
            correction.applied_at = datetime.now()
            applied.append(correction)
            applied_ids.append(correction.id)

        except Exception as e:
            failed.append({
                "correction_id": correction.id,
                "error": str(e),
            })
            failed_ids.append(correction.id)

    remaining = [c for c in all_corrections if not c.applied and not c.rolled_back and c.id not in failed_ids]
    remaining_count = len(remaining)
    skipped_count = len(skipped)

    apply_record = ApplyRecord(
        apply_id=str(uuid.uuid4())[:8],
        applied_at=datetime.now(),
        applied_count=len(applied),
        skipped_count=skipped_count,
        failed_count=len(failed),
        remaining_count=remaining_count,
        total_count=total_count,
        applied_ids=applied_ids,
        skipped_ids=skipped_ids,
        failed_ids=failed_ids,
        limit=limit,
        target_correction_id=correction_id,
        hash_mismatch_count=len(hash_mismatches),
    )

    batch.apply_records.append(apply_record)
    batch.last_apply_at = datetime.now()

    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "apply_id": apply_record.apply_id,
        "applied_count": len(applied),
        "skipped_count": skipped_count,
        "failed_count": len(failed),
        "remaining_count": remaining_count,
        "total_count": total_count,
        "hash_mismatch_count": len(hash_mismatches),
        "applied": [c.to_dict() for c in applied],
        "skipped": [c.to_dict() for c in skipped],
        "failed": failed,
        "hash_mismatches": hash_mismatches,
        "applied_ids": applied_ids,
        "skipped_ids": skipped_ids,
        "failed_ids": failed_ids,
        "limit": limit,
        "target_correction_id": correction_id,
    }


def apply_single_correction(
    config: Config,
    storage: BatchStorage,
    correction_id: str,
    batch_id: str = None,
) -> Dict:
    return apply_corrections(config, storage, batch_id, correction_id=correction_id)
