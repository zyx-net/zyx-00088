from typing import Dict, Optional

from ..config import Config
from ..models import CorrectionStatus, BatchHistory
from ..storage import BatchStorage
from .utils import get_or_create_batch


def get_batch_status(
    config: Config,
    storage: BatchStorage,
    batch_id: Optional[str] = None,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    stats = batch.get_statistics()

    pending_details = []
    completed_details = []
    conflicted_details = []
    failed_details = []
    skipped_details = []
    rolled_back_details = []

    for c in batch.corrections:
        info = {
            "id": c.id,
            "type": c.type.value,
            "source": c.source,
            "target": c.target,
            "reason": c.reason,
            "status": c.status.value,
            "applied": c.applied,
            "rolled_back": c.rolled_back,
            "applied_at": c.applied_at.isoformat() if c.applied_at else None,
            "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            "failure_reason": c.failure_reason,
            "plan_snapshot_id": c.plan_snapshot_id,
        }

        if c.status == CorrectionStatus.PENDING and not c.rolled_back:
            pending_details.append(info)
        elif c.status == CorrectionStatus.COMPLETED and not c.rolled_back:
            completed_details.append(info)
        elif c.status == CorrectionStatus.CONFLICTED:
            conflicted_details.append(info)
        elif c.status == CorrectionStatus.FAILED:
            failed_details.append(info)
        elif c.status == CorrectionStatus.SKIPPED:
            skipped_details.append(info)
        elif c.rolled_back or c.status == CorrectionStatus.ROLLED_BACK:
            rolled_back_details.append(info)

    snapshots = []
    for s in batch.plan_snapshots:
        snapshot_stats = _get_snapshot_stats(batch, s)
        snapshots.append({
            "snapshot_id": s.snapshot_id,
            "name": s.name,
            "created_at": s.created_at.isoformat(),
            "correction_count": len(s.correction_ids),
            "archive_dir": s.archive_dir,
            "description": s.description,
            "is_active": batch.active_snapshot_id == s.snapshot_id,
            **snapshot_stats,
        })

    result = {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "created_at": batch.created_at.isoformat(),
        "updated_at": batch.updated_at.isoformat(),
        "total_count": stats["total"],
        "pending_count": stats["pending"],
        "completed_count": stats["completed"],
        "conflicted_count": stats["conflicted"],
        "failed_count": stats["failed"],
        "skipped_count": stats["skipped"],
        "rolled_back_count": stats["rolled_back"],
        "undoable_count": stats["undoable"],
        "progress_percent": stats["completed"] * 100 // max(stats["total"], 1),
        "statistics": stats,
        "pending": pending_details,
        "completed": completed_details,
        "conflicted": conflicted_details,
        "failed": failed_details,
        "skipped": skipped_details,
        "rolled_back": rolled_back_details,
        "snapshots": snapshots,
        "active_snapshot_id": batch.active_snapshot_id,
        "last_plan_at": batch.last_plan_at.isoformat() if batch.last_plan_at else None,
        "last_apply_at": batch.last_apply_at.isoformat() if batch.last_apply_at else None,
        "last_undo_at": batch.last_undo_at.isoformat() if batch.last_undo_at else None,
        "file_count": len(batch.scanned_files),
        "delivery_count": len(batch.delivery_list),
    }

    if batch.conflicts:
        unresolved = [c for c in batch.conflicts if not c.resolved]
        result["unresolved_conflicts_count"] = len(unresolved)
        result["unresolved_conflicts"] = [c.to_dict() for c in unresolved]

    return result


def _get_snapshot_stats(batch: BatchHistory, snapshot) -> Dict:
    correction_ids = set(snapshot.correction_ids)
    pending = 0
    completed = 0
    conflicted = 0
    failed = 0
    skipped = 0
    rolled_back = 0

    for c in batch.corrections:
        if c.id not in correction_ids:
            continue
        if c.status == CorrectionStatus.PENDING and not c.rolled_back:
            pending += 1
        elif c.status == CorrectionStatus.COMPLETED and not c.rolled_back:
            completed += 1
        elif c.status == CorrectionStatus.CONFLICTED:
            conflicted += 1
        elif c.status == CorrectionStatus.FAILED:
            failed += 1
        elif c.status == CorrectionStatus.SKIPPED:
            skipped += 1
        elif c.rolled_back or c.status == CorrectionStatus.ROLLED_BACK:
            rolled_back += 1

    total = len(correction_ids)
    return {
        "snapshot_total": total,
        "snapshot_pending": pending,
        "snapshot_completed": completed,
        "snapshot_conflicted": conflicted,
        "snapshot_failed": failed,
        "snapshot_skipped": skipped,
        "snapshot_rolled_back": rolled_back,
        "snapshot_progress": completed * 100 // max(total, 1),
    }
