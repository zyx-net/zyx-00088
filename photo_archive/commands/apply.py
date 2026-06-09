from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import shutil
import uuid

from ..config import Config
from ..models import (
    ApplyRecord,
    BatchHistory,
    Conflict,
    ConflictType,
    CorrectionAction,
    CorrectionStatus,
    CorrectionType,
    FileStatus,
)
from ..scanner import FileScanner
from ..storage import BatchStorage
from .utils import get_or_create_batch


def _check_target_conflict(
    correction: CorrectionAction,
    scanner: FileScanner,
) -> Optional[Dict]:
    target = Path(correction.target)
    if not target.exists():
        return None

    current_hash = scanner.calculate_hash(target)

    if correction.expected_target_hash and correction.expected_target_hash != current_hash:
        return {
            "conflict_type": ConflictType.EXTERNAL_MODIFICATION.value,
            "message": f"目标文件已被外部修改: {correction.target}",
            "details": {
                "target_path": str(target),
                "expected_hash": correction.expected_target_hash,
                "actual_hash": current_hash,
                "file_size": target.stat().st_size,
                "modified_at": datetime.fromtimestamp(target.stat().st_mtime).isoformat(),
            },
        }

    if correction.actual_target_hash and correction.actual_target_hash != current_hash:
        return {
            "conflict_type": ConflictType.CONTENT_MISMATCH.value,
            "message": f"目标文件内容与记录不匹配: {correction.target}",
            "details": {
                "target_path": str(target),
                "recorded_hash": correction.actual_target_hash,
                "actual_hash": current_hash,
            },
        }

    return None


def apply_corrections(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
    correction_id: Optional[str] = None,
    limit: Optional[int] = None,
    from_snapshot: Optional[str] = None,
    resume: bool = True,
    skip_conflicts: bool = False,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    if not batch.corrections:
        raise ValueError("没有可应用的修正计划，请先生成修正计划")

    all_corrections = batch.corrections
    total_count = len(all_corrections)

    snapshot_correction_ids = None
    if from_snapshot:
        snapshot = next((s for s in batch.plan_snapshots if s.snapshot_id == from_snapshot), None)
        if not snapshot:
            raise ValueError(f"找不到快照: {from_snapshot}")
        snapshot_correction_ids = set(snapshot.correction_ids)
        batch.active_snapshot_id = from_snapshot

    applied_before = [c for c in all_corrections if c.status == CorrectionStatus.COMPLETED and not c.rolled_back]
    skipped_completed = []
    conflicts: List[Dict] = []

    pending = []
    for c in all_corrections:
        if snapshot_correction_ids and c.id not in snapshot_correction_ids:
            continue

        if c.status == CorrectionStatus.COMPLETED and not c.rolled_back:
            if resume:
                skipped_completed.append(c)
            else:
                pending.append(c)
        elif c.status == CorrectionStatus.CONFLICTED and not c.rolled_back:
            if resume and not skip_conflicts:
                conflicts.append({
                    "correction_id": c.id,
                    "conflict_type": c.conflict_details.get("conflict_type") if c.conflict_details else "unknown",
                    "message": c.failure_reason or "存在未解决的冲突",
                })
            else:
                pending.append(c)
        elif c.status in (CorrectionStatus.PENDING, CorrectionStatus.FAILED) and not c.rolled_back:
            pending.append(c)

    if correction_id:
        target_correction = next((c for c in pending if c.id == correction_id), None)
        skipped_completed = []
        if not target_correction:
            already_completed = next((c for c in all_corrections if c.id == correction_id), None)
            if already_completed:
                if already_completed.status == CorrectionStatus.COMPLETED and not already_completed.rolled_back:
                    skipped_completed.append(already_completed)
                    pending = []
                elif already_completed.rolled_back:
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
    new_conflicts: List[Dict] = []
    applied_ids: List[str] = []
    failed_ids: List[str] = []
    conflicted_ids: List[str] = []
    skipped_completed_ids = [c.id for c in skipped_completed]

    for correction in pending:
        try:
            if correction.type in (CorrectionType.COPY, CorrectionType.MOVE):
                source = Path(correction.source)
                target = Path(correction.target)
                target.parent.mkdir(parents=True, exist_ok=True)

                if not source.exists():
                    raise FileNotFoundError(f"源文件不存在: {correction.source}")

                source_str = str(source.resolve())
                expected_hash = correction.source_hash
                if not expected_hash and source_str in batch.scanned_files:
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
                    correction.status = CorrectionStatus.FAILED
                    correction.failure_reason = f"源文件哈希不一致: 扫描后文件已被篡改，源: {source_str}"
                    failed.append({
                        "correction_id": correction.id,
                        "error": correction.failure_reason,
                    })
                    failed_ids.append(correction.id)

                    for item in batch.delivery_list.values():
                        if item.matched_source == source_str:
                            item.status = FileStatus.HASH_MISMATCH
                            item.actual_hash = current_hash
                    continue

                conflict = _check_target_conflict(correction, scanner)
                if conflict:
                    correction.status = CorrectionStatus.CONFLICTED
                    correction.conflict_details = conflict
                    correction.failure_reason = conflict["message"]
                    new_conflicts.append({
                        "correction_id": correction.id,
                        **conflict,
                    })
                    conflicted_ids.append(correction.id)

                    batch_conflict = Conflict(
                        conflict_id=str(uuid.uuid4())[:8],
                        conflict_type=ConflictType(conflict["conflict_type"]),
                        target_name=Path(correction.target).name,
                        source_file=correction.source,
                        batch_name=batch.name,
                        message=conflict["message"],
                        details=conflict["details"],
                        resolved=False,
                    )
                    batch.conflicts.append(batch_conflict)
                    continue

                if correction.type == CorrectionType.COPY:
                    shutil.copy2(source, target)
                else:
                    shutil.move(source, target)

                correction.actual_target_hash = scanner.calculate_hash(target)

            elif correction.type == CorrectionType.DELETE:
                target = Path(correction.target)
                if target.exists():
                    target.unlink()

            elif correction.type == CorrectionType.SKIP:
                pass

            correction.applied = True
            correction.applied_at = datetime.now()
            correction.completed_at = datetime.now()
            correction.status = CorrectionStatus.COMPLETED
            correction.failure_reason = None
            correction.conflict_details = None
            applied.append(correction)
            applied_ids.append(correction.id)

        except Exception as e:
            correction.status = CorrectionStatus.FAILED
            correction.failure_reason = str(e)
            failed.append({
                "correction_id": correction.id,
                "error": str(e),
            })
            failed_ids.append(correction.id)

    remaining = [c for c in all_corrections if c.status == CorrectionStatus.PENDING and not c.rolled_back and c.id not in failed_ids and c.id not in conflicted_ids]
    if snapshot_correction_ids:
        remaining = [c for c in remaining if c.id in snapshot_correction_ids]
    remaining_count = len(remaining)
    skipped_count = len(skipped_completed)

    apply_record = ApplyRecord(
        apply_id=str(uuid.uuid4())[:8],
        applied_at=datetime.now(),
        applied_count=len(applied),
        skipped_count=skipped_count,
        failed_count=len(failed),
        remaining_count=remaining_count,
        total_count=total_count,
        applied_ids=applied_ids,
        skipped_ids=skipped_completed_ids,
        failed_ids=failed_ids,
        limit=limit,
        target_correction_id=correction_id,
        hash_mismatch_count=len(hash_mismatches),
    )

    batch.apply_records.append(apply_record)
    batch.last_apply_at = datetime.now()

    storage.save(batch)

    stats = batch.get_statistics()
    all_conflicts = conflicts + new_conflicts

    result = {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "apply_id": apply_record.apply_id,
        "applied_count": len(applied),
        "skipped_count": skipped_count,
        "failed_count": len(failed),
        "conflict_count": len(all_conflicts),
        "remaining_count": remaining_count,
        "total_count": total_count,
        "hash_mismatch_count": len(hash_mismatches),
        "from_snapshot": from_snapshot,
        "applied": [c.to_dict() for c in applied],
        "skipped": [c.to_dict() for c in skipped_completed],
        "failed": failed,
        "conflicts": all_conflicts,
        "hash_mismatches": hash_mismatches,
        "applied_ids": applied_ids,
        "skipped_ids": skipped_completed_ids,
        "failed_ids": failed_ids,
        "conflicted_ids": conflicted_ids,
        "limit": limit,
        "target_correction_id": correction_id,
        "statistics": stats,
    }

    return result


def apply_single_correction(
    config: Config,
    storage: BatchStorage,
    correction_id: str,
    batch_id: str = None,
) -> Dict:
    return apply_corrections(config, storage, batch_id, correction_id=correction_id)
