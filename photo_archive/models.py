from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
import json
import uuid

if TYPE_CHECKING:
    from .config import Config


class ConflictType(str, Enum):
    TARGET_NAME_CONFLICT = "target_name_conflict"
    SOURCE_FILE_CONFLICT = "source_file_conflict"
    BATCH_NAME_CONFLICT = "batch_name_conflict"
    EXTERNAL_MODIFICATION = "external_modification"
    CONTENT_MISMATCH = "content_mismatch"


class MergeStatus(str, Enum):
    PENDING = "pending"
    CONFLICT = "conflict"
    OK = "ok"


class CorrectionType(str, Enum):
    MOVE = "move"
    RENAME = "rename"
    COPY = "copy"
    DELETE = "delete"
    SKIP = "skip"


class FileStatus(str, Enum):
    UNKNOWN = "unknown"
    OK = "ok"
    MISSING = "missing"
    DUPLICATE = "duplicate"
    HASH_MISMATCH = "hash_mismatch"
    CORRUPTED = "corrupted"


class CorrectionStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CONFLICTED = "conflicted"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


@dataclass
class ScannedFile:
    original_path: str
    file_name: str
    size: int
    hash: str
    extension: str
    camera: Optional[str] = None
    detected_batch: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "original_path": self.original_path,
            "file_name": self.file_name,
            "size": self.size,
            "hash": self.hash,
            "extension": self.extension,
            "camera": self.camera,
            "detected_batch": self.detected_batch,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ScannedFile":
        return cls(
            original_path=data["original_path"],
            file_name=data["file_name"],
            size=data["size"],
            hash=data["hash"],
            extension=data["extension"],
            camera=data.get("camera"),
            detected_batch=data.get("detected_batch"),
        )


@dataclass
class DeliveryItem:
    target_name: str
    expected_hash: Optional[str] = None
    expected_camera: Optional[str] = None
    expected_size: Optional[int] = None
    sequence: Optional[int] = None
    status: FileStatus = FileStatus.UNKNOWN
    matched_source: Optional[str] = None
    actual_hash: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "target_name": self.target_name,
            "expected_hash": self.expected_hash,
            "expected_camera": self.expected_camera,
            "expected_size": self.expected_size,
            "sequence": self.sequence,
            "status": self.status.value,
            "matched_source": self.matched_source,
            "actual_hash": self.actual_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DeliveryItem":
        return cls(
            target_name=data["target_name"],
            expected_hash=data.get("expected_hash"),
            expected_camera=data.get("expected_camera"),
            expected_size=data.get("expected_size"),
            sequence=data.get("sequence"),
            status=FileStatus(data.get("status", FileStatus.UNKNOWN.value)),
            matched_source=data.get("matched_source"),
            actual_hash=data.get("actual_hash"),
        )


@dataclass
class CorrectionAction:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: CorrectionType = CorrectionType.MOVE
    source: str = ""
    target: str = ""
    reason: str = ""
    status: CorrectionStatus = CorrectionStatus.PENDING
    applied: bool = False
    applied_at: Optional[datetime] = None
    rolled_back: bool = False
    source_hash: Optional[str] = None
    expected_target_hash: Optional[str] = None
    actual_target_hash: Optional[str] = None
    failure_reason: Optional[str] = None
    conflict_details: Optional[Dict] = None
    completed_at: Optional[datetime] = None
    plan_snapshot_id: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "source": self.source,
            "target": self.target,
            "reason": self.reason,
            "status": self.status.value,
            "applied": self.applied,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "rolled_back": self.rolled_back,
            "source_hash": self.source_hash,
            "expected_target_hash": self.expected_target_hash,
            "actual_target_hash": self.actual_target_hash,
            "failure_reason": self.failure_reason,
            "conflict_details": self.conflict_details,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "plan_snapshot_id": self.plan_snapshot_id,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "CorrectionAction":
        return cls(
            id=data["id"],
            type=CorrectionType(data["type"]),
            source=data["source"],
            target=data["target"],
            reason=data["reason"],
            status=CorrectionStatus(data.get("status", CorrectionStatus.PENDING.value)),
            applied=data.get("applied", False),
            applied_at=datetime.fromisoformat(data["applied_at"]) if data.get("applied_at") else None,
            rolled_back=data.get("rolled_back", False),
            source_hash=data.get("source_hash"),
            expected_target_hash=data.get("expected_target_hash"),
            actual_target_hash=data.get("actual_target_hash"),
            failure_reason=data.get("failure_reason"),
            conflict_details=data.get("conflict_details"),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            plan_snapshot_id=data.get("plan_snapshot_id"),
        )


@dataclass
class ImportRecord:
    import_id: str
    source_file: str
    imported_at: datetime
    imported_count: int
    batch_name: str
    items: List[Dict] = field(default_factory=list)
    dry_run: bool = False
    conflict_summary: Dict = field(default_factory=dict)
    added_items: List[str] = field(default_factory=list)
    skipped_items: List[str] = field(default_factory=list)
    conflicted_items: List[str] = field(default_factory=list)
    overwritten_items: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "import_id": self.import_id,
            "source_file": self.source_file,
            "imported_at": self.imported_at.isoformat(),
            "imported_count": self.imported_count,
            "batch_name": self.batch_name,
            "items": self.items,
            "dry_run": self.dry_run,
            "conflict_summary": self.conflict_summary,
            "added_items": self.added_items,
            "skipped_items": self.skipped_items,
            "conflicted_items": self.conflicted_items,
            "overwritten_items": self.overwritten_items,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ImportRecord":
        return cls(
            import_id=data["import_id"],
            source_file=data["source_file"],
            imported_at=datetime.fromisoformat(data["imported_at"]),
            imported_count=data["imported_count"],
            batch_name=data["batch_name"],
            items=data.get("items", []),
            dry_run=data.get("dry_run", False),
            conflict_summary=data.get("conflict_summary", {}),
            added_items=data.get("added_items", []),
            skipped_items=data.get("skipped_items", []),
            conflicted_items=data.get("conflicted_items", []),
            overwritten_items=data.get("overwritten_items", []),
        )


@dataclass
class Conflict:
    conflict_id: str
    conflict_type: ConflictType
    target_name: Optional[str] = None
    source_file: Optional[str] = None
    batch_name: Optional[str] = None
    message: str = ""
    details: Dict = field(default_factory=dict)
    resolved: bool = False
    resolved_at: Optional[datetime] = None

    def to_dict(self) -> Dict:
        return {
            "conflict_id": self.conflict_id,
            "conflict_type": self.conflict_type.value,
            "target_name": self.target_name,
            "source_file": self.source_file,
            "batch_name": self.batch_name,
            "message": self.message,
            "details": self.details,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Conflict":
        return cls(
            conflict_id=data["conflict_id"],
            conflict_type=ConflictType(data["conflict_type"]),
            target_name=data.get("target_name"),
            source_file=data.get("source_file"),
            batch_name=data.get("batch_name"),
            message=data.get("message", ""),
            details=data.get("details", {}),
            resolved=data.get("resolved", False),
            resolved_at=datetime.fromisoformat(data["resolved_at"]) if data.get("resolved_at") else None,
        )


@dataclass
class ApplyRecord:
    apply_id: str
    applied_at: datetime
    applied_count: int
    skipped_count: int
    failed_count: int
    remaining_count: int
    total_count: int
    applied_ids: List[str] = field(default_factory=list)
    skipped_ids: List[str] = field(default_factory=list)
    failed_ids: List[str] = field(default_factory=list)
    limit: Optional[int] = None
    target_correction_id: Optional[str] = None
    hash_mismatch_count: int = 0

    def to_dict(self) -> Dict:
        return {
            "apply_id": self.apply_id,
            "applied_at": self.applied_at.isoformat(),
            "applied_count": self.applied_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "remaining_count": self.remaining_count,
            "total_count": self.total_count,
            "applied_ids": self.applied_ids,
            "skipped_ids": self.skipped_ids,
            "failed_ids": self.failed_ids,
            "limit": self.limit,
            "target_correction_id": self.target_correction_id,
            "hash_mismatch_count": self.hash_mismatch_count,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ApplyRecord":
        return cls(
            apply_id=data["apply_id"],
            applied_at=datetime.fromisoformat(data["applied_at"]),
            applied_count=data["applied_count"],
            skipped_count=data["skipped_count"],
            failed_count=data["failed_count"],
            remaining_count=data["remaining_count"],
            total_count=data["total_count"],
            applied_ids=data.get("applied_ids", []),
            skipped_ids=data.get("skipped_ids", []),
            failed_ids=data.get("failed_ids", []),
            limit=data.get("limit"),
            target_correction_id=data.get("target_correction_id"),
            hash_mismatch_count=data.get("hash_mismatch_count", 0),
        )


@dataclass
class UndoRecord:
    undo_id: str
    undone_at: datetime
    undone_count: int
    failed_count: int
    total_applied_before: int
    remaining_applied_after: int
    undone_ids: List[str] = field(default_factory=list)
    failed_ids: List[str] = field(default_factory=list)
    target_correction_id: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "undo_id": self.undo_id,
            "undone_at": self.undone_at.isoformat(),
            "undone_count": self.undone_count,
            "failed_count": self.failed_count,
            "total_applied_before": self.total_applied_before,
            "remaining_applied_after": self.remaining_applied_after,
            "undone_ids": self.undone_ids,
            "failed_ids": self.failed_ids,
            "target_correction_id": self.target_correction_id,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "UndoRecord":
        return cls(
            undo_id=data["undo_id"],
            undone_at=datetime.fromisoformat(data["undone_at"]),
            undone_count=data["undone_count"],
            failed_count=data["failed_count"],
            total_applied_before=data["total_applied_before"],
            remaining_applied_after=data["remaining_applied_after"],
            undone_ids=data.get("undone_ids", []),
            failed_ids=data.get("failed_ids", []),
            target_correction_id=data.get("target_correction_id"),
        )


@dataclass
class PlanSnapshot:
    snapshot_id: str
    name: str
    created_at: datetime
    correction_ids: List[str] = field(default_factory=list)
    archive_dir: str = ""
    description: str = ""

    def to_dict(self) -> Dict:
        return {
            "snapshot_id": self.snapshot_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "correction_ids": self.correction_ids,
            "archive_dir": self.archive_dir,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PlanSnapshot":
        return cls(
            snapshot_id=data["snapshot_id"],
            name=data["name"],
            created_at=datetime.fromisoformat(data["created_at"]),
            correction_ids=data.get("correction_ids", []),
            archive_dir=data.get("archive_dir", ""),
            description=data.get("description", ""),
        )


@dataclass
class BatchHistory:
    batch_id: str
    name: str
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    scanned_files: Dict[str, ScannedFile] = field(default_factory=dict)
    delivery_list: Dict[str, DeliveryItem] = field(default_factory=dict)
    corrections: List[CorrectionAction] = field(default_factory=list)
    import_records: List[ImportRecord] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    apply_records: List[ApplyRecord] = field(default_factory=list)
    undo_records: List[UndoRecord] = field(default_factory=list)
    plan_snapshots: List[PlanSnapshot] = field(default_factory=list)
    merge_status: MergeStatus = MergeStatus.PENDING
    scan_source_dir: Optional[str] = None
    last_scan_at: Optional[datetime] = None
    last_import_at: Optional[datetime] = None
    last_verify_at: Optional[datetime] = None
    last_apply_at: Optional[datetime] = None
    last_undo_at: Optional[datetime] = None
    last_plan_at: Optional[datetime] = None
    active_snapshot_id: Optional[str] = None
    normalized_name: str = ""

    def __post_init__(self):
        if not self.normalized_name:
            self.normalized_name = self._normalize_name(self.name)

    @staticmethod
    def _normalize_name(name: str) -> str:
        return " ".join(name.strip().lower().split())

    def get_statistics(self) -> Dict:
        pending = 0
        completed = 0
        conflicted = 0
        failed = 0
        skipped = 0
        rolled_back = 0
        undoable = 0

        for c in self.corrections:
            if c.status == CorrectionStatus.PENDING and not c.rolled_back:
                pending += 1
            elif c.status == CorrectionStatus.COMPLETED and not c.rolled_back:
                completed += 1
                if not c.rolled_back:
                    undoable += 1
            elif c.status == CorrectionStatus.CONFLICTED:
                conflicted += 1
            elif c.status == CorrectionStatus.FAILED:
                failed += 1
            elif c.status == CorrectionStatus.SKIPPED:
                skipped += 1
            elif c.rolled_back or c.status == CorrectionStatus.ROLLED_BACK:
                rolled_back += 1

        return {
            "total": len(self.corrections),
            "pending": pending,
            "completed": completed,
            "conflicted": conflicted,
            "failed": failed,
            "skipped": skipped,
            "rolled_back": rolled_back,
            "undoable": undoable,
        }

    def to_dict(self) -> Dict:
        return {
            "batch_id": self.batch_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "scanned_files": {k: v.to_dict() for k, v in self.scanned_files.items()},
            "delivery_list": {k: v.to_dict() for k, v in self.delivery_list.items()},
            "corrections": [c.to_dict() for c in self.corrections],
            "import_records": [r.to_dict() for r in self.import_records],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "apply_records": [r.to_dict() for r in self.apply_records],
            "undo_records": [r.to_dict() for r in self.undo_records],
            "plan_snapshots": [s.to_dict() for s in self.plan_snapshots],
            "merge_status": self.merge_status.value,
            "scan_source_dir": self.scan_source_dir,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "last_import_at": self.last_import_at.isoformat() if self.last_import_at else None,
            "last_verify_at": self.last_verify_at.isoformat() if self.last_verify_at else None,
            "last_apply_at": self.last_apply_at.isoformat() if self.last_apply_at else None,
            "last_undo_at": self.last_undo_at.isoformat() if self.last_undo_at else None,
            "last_plan_at": self.last_plan_at.isoformat() if self.last_plan_at else None,
            "active_snapshot_id": self.active_snapshot_id,
            "normalized_name": self.normalized_name,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "BatchHistory":
        return cls(
            batch_id=data["batch_id"],
            name=data["name"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            scanned_files={k: ScannedFile.from_dict(v) for k, v in data.get("scanned_files", {}).items()},
            delivery_list={k: DeliveryItem.from_dict(v) for k, v in data.get("delivery_list", {}).items()},
            corrections=[CorrectionAction.from_dict(c) for c in data.get("corrections", [])],
            import_records=[ImportRecord.from_dict(r) for r in data.get("import_records", [])],
            conflicts=[Conflict.from_dict(c) for c in data.get("conflicts", [])],
            apply_records=[ApplyRecord.from_dict(r) for r in data.get("apply_records", [])],
            undo_records=[UndoRecord.from_dict(r) for r in data.get("undo_records", [])],
            plan_snapshots=[PlanSnapshot.from_dict(s) for s in data.get("plan_snapshots", [])],
            merge_status=MergeStatus(data.get("merge_status", MergeStatus.PENDING.value)),
            scan_source_dir=data.get("scan_source_dir"),
            last_scan_at=datetime.fromisoformat(data["last_scan_at"]) if data.get("last_scan_at") else None,
            last_import_at=datetime.fromisoformat(data["last_import_at"]) if data.get("last_import_at") else None,
            last_verify_at=datetime.fromisoformat(data["last_verify_at"]) if data.get("last_verify_at") else None,
            last_apply_at=datetime.fromisoformat(data["last_apply_at"]) if data.get("last_apply_at") else None,
            last_undo_at=datetime.fromisoformat(data["last_undo_at"]) if data.get("last_undo_at") else None,
            last_plan_at=datetime.fromisoformat(data["last_plan_at"]) if data.get("last_plan_at") else None,
            active_snapshot_id=data.get("active_snapshot_id"),
            normalized_name=data.get("normalized_name", ""),
        )


@dataclass
class Profile:
    PROFILE_VERSION = 2
    name: str
    description: str = ""
    version: int = PROFILE_VERSION
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    naming_rule: str = "{机位}_{批次}_{序号:04d}.{扩展名}"
    cameras: List[str] = field(default_factory=lambda: ["A", "B", "C"])
    hash_strategy: str = "sha256"
    archive_dir: str = "./archive"
    work_dir: str = "./work"
    conflict_strategy: str = "fail"
    resume: bool = True
    skip_conflicts: bool = False
    output_format: str = "text"
    log_level: str = "INFO"
    default_limit: Optional[int] = None

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "naming_rule": self.naming_rule,
            "cameras": self.cameras,
            "hash_strategy": self.hash_strategy,
            "archive_dir": self.archive_dir,
            "work_dir": self.work_dir,
            "conflict_strategy": self.conflict_strategy,
            "resume": self.resume,
            "skip_conflicts": self.skip_conflicts,
            "output_format": self.output_format,
            "log_level": self.log_level,
            "default_limit": self.default_limit,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Profile":
        version = data.get("version", data.get("profile_version", 1))
        if version < cls.PROFILE_VERSION:
            data = cls._migrate(data, version)
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            version=cls.PROFILE_VERSION,
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
            naming_rule=data.get("naming_rule", "{机位}_{批次}_{序号:04d}.{扩展名}"),
            cameras=data.get("cameras", ["A", "B", "C"]),
            hash_strategy=data.get("hash_strategy", "sha256"),
            archive_dir=data.get("archive_dir", "./archive"),
            work_dir=data.get("work_dir", "./work"),
            conflict_strategy=data.get("conflict_strategy", "fail"),
            resume=data.get("resume", True),
            skip_conflicts=data.get("skip_conflicts", False),
            output_format=data.get("output_format", "text"),
            log_level=data.get("log_level", "INFO"),
            default_limit=data.get("default_limit", None),
        )

    @staticmethod
    def _migrate(data: Dict, from_version: int) -> Dict:
        if from_version < 2:
            data.setdefault("conflict_strategy", "fail")
            data.setdefault("resume", True)
            data.setdefault("skip_conflicts", False)
            data.setdefault("output_format", "text")
            data.setdefault("log_level", "INFO")
            data.setdefault("default_limit", None)
        data["version"] = Profile.PROFILE_VERSION
        return data

    def apply_to_config(self, config: Config) -> None:
        config.naming_rule = self.naming_rule
        config.cameras = list(self.cameras)
        config.hash_strategy = self.hash_strategy
        config.archive_dir = Path(self.archive_dir)
        config.work_dir = Path(self.work_dir)

    @classmethod
    def from_config(cls, name: str, config: Config, description: str = "") -> "Profile":
        return cls(
            name=name,
            description=description,
            naming_rule=config.naming_rule,
            cameras=list(config.cameras),
            hash_strategy=config.hash_strategy,
            archive_dir=str(config.archive_dir),
            work_dir=str(config.work_dir),
        )


class BatchNameConflictError(Exception):
    """批次名归一化冲突异常

    当新批次名与现有批次归一化后相同但原始名称不同时抛出。
    例如 "Wedding 2024" 与 "wedding  2024" 归一化后相同。
    """

    def __init__(
        self,
        requested_name: str,
        normalized_name: str,
        conflicting_batches: List[BatchHistory],
        message: Optional[str] = None,
    ):
        self.requested_name = requested_name
        self.normalized_name = normalized_name
        self.conflicting_batches = conflicting_batches
        if message is None:
            existing_names = ", ".join(f'"{b.name}" ({b.batch_id})' for b in conflicting_batches)
            message = (
                f'批次名 "{requested_name}" 归一化后为 "{normalized_name}"，'
                f"与现有批次冲突: {existing_names}"
            )
        super().__init__(message)

    def to_dict(self) -> Dict:
        return {
            "error": "batch_name_conflict",
            "requested_name": self.requested_name,
            "normalized_name": self.normalized_name,
            "conflicting_batches": [
                {
                    "batch_id": b.batch_id,
                    "name": b.name,
                    "normalized_name": b.normalized_name,
                    "created_at": b.created_at.isoformat(),
                }
                for b in self.conflicting_batches
            ],
            "message": str(self),
        }


class ProfileNameConflictError(Exception):
    """Profile 名称冲突异常"""

    def __init__(self, profile_name: str, existing_profile: Profile = None, message: Optional[str] = None):
        self.profile_name = profile_name
        self.existing_profile = existing_profile
        if message is None:
            message = f'Profile "{profile_name}" 已存在'
        super().__init__(message)

    def to_dict(self) -> Dict:
        return {
            "error": "profile_name_conflict",
            "profile_name": self.profile_name,
            "existing_profile": self.existing_profile.to_dict() if self.existing_profile else None,
            "message": str(self),
        }


class PackageStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DRY_RUN = "dry_run"


@dataclass
class PackageFileItem:
    source_path: str
    target_path: str
    file_name: str
    size: int
    hash: str
    camera: Optional[str] = None
    status: FileStatus = FileStatus.OK
    copied: bool = False
    skip_reason: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "source_path": self.source_path,
            "target_path": self.target_path,
            "file_name": self.file_name,
            "size": self.size,
            "hash": self.hash,
            "camera": self.camera,
            "status": self.status.value,
            "copied": self.copied,
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PackageFileItem":
        return cls(
            source_path=data["source_path"],
            target_path=data["target_path"],
            file_name=data["file_name"],
            size=data["size"],
            hash=data["hash"],
            camera=data.get("camera"),
            status=FileStatus(data.get("status", FileStatus.OK.value)),
            copied=data.get("copied", False),
            skip_reason=data.get("skip_reason"),
        )


@dataclass
class PackageRecord:
    package_id: str
    batch_id: str
    batch_name: str
    target_dir: str
    status: PackageStatus
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_files: int = 0
    total_size: int = 0
    copied_files: int = 0
    copied_size: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    dry_run: bool = False
    items: List[PackageFileItem] = field(default_factory=list)
    skipped_items: List[PackageFileItem] = field(default_factory=list)
    failed_items: List[Dict] = field(default_factory=list)
    input_batches: List[str] = field(default_factory=list)
    manifest_path: Optional[str] = None
    checksum_path: Optional[str] = None
    readme_path: Optional[str] = None
    package_structure: Dict = field(default_factory=dict)
    notes: str = ""
    error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "package_id": self.package_id,
            "batch_id": self.batch_id,
            "batch_name": self.batch_name,
            "target_dir": self.target_dir,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_files": self.total_files,
            "total_size": self.total_size,
            "copied_files": self.copied_files,
            "copied_size": self.copied_size,
            "skipped_files": self.skipped_files,
            "failed_files": self.failed_files,
            "dry_run": self.dry_run,
            "items": [i.to_dict() for i in self.items],
            "skipped_items": [i.to_dict() for i in self.skipped_items],
            "failed_items": self.failed_items,
            "input_batches": self.input_batches,
            "manifest_path": self.manifest_path,
            "checksum_path": self.checksum_path,
            "readme_path": self.readme_path,
            "package_structure": self.package_structure,
            "notes": self.notes,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PackageRecord":
        return cls(
            package_id=data["package_id"],
            batch_id=data["batch_id"],
            batch_name=data["batch_name"],
            target_dir=data["target_dir"],
            status=PackageStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            total_files=data.get("total_files", 0),
            total_size=data.get("total_size", 0),
            copied_files=data.get("copied_files", 0),
            copied_size=data.get("copied_size", 0),
            skipped_files=data.get("skipped_files", 0),
            failed_files=data.get("failed_files", 0),
            dry_run=data.get("dry_run", False),
            items=[PackageFileItem.from_dict(i) for i in data.get("items", [])],
            skipped_items=[PackageFileItem.from_dict(i) for i in data.get("skipped_items", [])],
            failed_items=data.get("failed_items", []),
            input_batches=data.get("input_batches", []),
            manifest_path=data.get("manifest_path"),
            checksum_path=data.get("checksum_path"),
            readme_path=data.get("readme_path"),
            package_structure=data.get("package_structure", {}),
            notes=data.get("notes", ""),
            error_message=data.get("error_message"),
        )


class PackageTargetExistsError(Exception):
    """打包目标目录已存在异常"""

    def __init__(self, target_dir: str, message: Optional[str] = None):
        self.target_dir = target_dir
        if message is None:
            message = f'目标目录已存在: {target_dir}，请删除或使用其他目录'
        super().__init__(message)

    def to_dict(self) -> Dict:
        return {
            "error": "package_target_exists",
            "target_dir": self.target_dir,
            "message": str(self),
        }


class PackageFileConflictError(Exception):
    """打包文件冲突异常"""

    def __init__(self, file_name: str, target_path: str, message: Optional[str] = None):
        self.file_name = file_name
        self.target_path = target_path
        if message is None:
            message = f'目标文件已存在且内容不匹配: {target_path}'
        super().__init__(message)

    def to_dict(self) -> Dict:
        return {
            "error": "package_file_conflict",
            "file_name": self.file_name,
            "target_path": self.target_path,
            "message": str(self),
        }


class PackageInsufficientSpaceError(Exception):
    """磁盘空间不足异常"""

    def __init__(self, required: int, available: int, target_dir: str, message: Optional[str] = None):
        self.required = required
        self.available = available
        self.target_dir = target_dir
        if message is None:
            message = (f'磁盘空间不足: 需要 {required} bytes，可用 {available} bytes，'
                       f'缺少 {required - available} bytes')
        super().__init__(message)

    def to_dict(self) -> Dict:
        return {
            "error": "package_insufficient_space",
            "required": self.required,
            "available": self.available,
            "target_dir": self.target_dir,
            "message": str(self),
        }


class PackageSourceModifiedError(Exception):
    """源文件被篡改异常"""

    def __init__(self, source_path: str, expected_hash: str, actual_hash: str, message: Optional[str] = None):
        self.source_path = source_path
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        if message is None:
            message = f'源文件已被篡改: {source_path}，期望哈希: {expected_hash[:16]}...，实际哈希: {actual_hash[:16]}...'
        super().__init__(message)

    def to_dict(self) -> Dict:
        return {
            "error": "package_source_modified",
            "source_path": self.source_path,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "message": str(self),
        }


@dataclass
class AuditLogEntry:
    entry_id: str
    timestamp: datetime
    operation: str
    profile_name: Optional[str] = None
    details: Dict = field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "operation": self.operation,
            "profile_name": self.profile_name,
            "details": self.details,
            "success": self.success,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AuditLogEntry":
        return cls(
            entry_id=data["entry_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            operation=data["operation"],
            profile_name=data.get("profile_name"),
            details=data.get("details", {}),
            success=data.get("success", True),
            error_message=data.get("error_message"),
        )


class AcceptanceCheckStatus(str, Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


class AcceptanceCheckType(str, Enum):
    DIRECTORY_STRUCTURE = "directory_structure"
    REQUIRED_FILES = "required_files"
    PHOTO_COUNT = "photo_count"
    FILE_SIZE = "file_size"
    EXTENSION = "extension"
    DUPLICATE_FILENAME = "duplicate_filename"
    MISSING_MANIFEST = "missing_manifest"


@dataclass
class AcceptanceRule:
    rule_id: str
    rule_type: AcceptanceCheckType
    description: str = ""
    enabled: bool = True
    parameters: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type.value,
            "description": self.description,
            "enabled": self.enabled,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AcceptanceRule":
        return cls(
            rule_id=data["rule_id"],
            rule_type=AcceptanceCheckType(data["rule_type"]),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            parameters=data.get("parameters", {}),
        )


@dataclass
class AcceptanceCheckResult:
    rule_id: str
    rule_type: AcceptanceCheckType
    status: AcceptanceCheckStatus
    message: str = ""
    details: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type.value,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AcceptanceCheckResult":
        return cls(
            rule_id=data["rule_id"],
            rule_type=AcceptanceCheckType(data["rule_type"]),
            status=AcceptanceCheckStatus(data["status"]),
            message=data.get("message", ""),
            details=data.get("details", []),
        )


@dataclass
class AcceptanceAuditConfig:
    client_name: str
    batch_name: str
    source_dir: str
    rules: List[AcceptanceRule] = field(default_factory=list)
    manifest_file: Optional[str] = None
    expected_photo_count: Optional[int] = None
    allowed_extensions: List[str] = field(default_factory=list)
    min_file_size: Optional[int] = None
    max_file_size: Optional[int] = None
    required_directories: List[str] = field(default_factory=list)
    required_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "client_name": self.client_name,
            "batch_name": self.batch_name,
            "source_dir": self.source_dir,
            "rules": [r.to_dict() for r in self.rules],
            "manifest_file": self.manifest_file,
            "expected_photo_count": self.expected_photo_count,
            "allowed_extensions": self.allowed_extensions,
            "min_file_size": self.min_file_size,
            "max_file_size": self.max_file_size,
            "required_directories": self.required_directories,
            "required_files": self.required_files,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AcceptanceAuditConfig":
        return cls(
            client_name=data["client_name"],
            batch_name=data["batch_name"],
            source_dir=data["source_dir"],
            rules=[AcceptanceRule.from_dict(r) for r in data.get("rules", [])],
            manifest_file=data.get("manifest_file"),
            expected_photo_count=data.get("expected_photo_count"),
            allowed_extensions=data.get("allowed_extensions", []),
            min_file_size=data.get("min_file_size"),
            max_file_size=data.get("max_file_size"),
            required_directories=data.get("required_directories", []),
            required_files=data.get("required_files", []),
        )


@dataclass
class AcceptanceAuditRecord:
    audit_id: str
    client_name: str
    batch_name: str
    source_dir: str
    started_at: datetime
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    status: AcceptanceCheckStatus = AcceptanceCheckStatus.PENDING
    config: Optional[AcceptanceAuditConfig] = None
    results: List[AcceptanceCheckResult] = field(default_factory=list)
    exported_paths: Dict[str, str] = field(default_factory=dict)
    log_entries: List[Dict] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "audit_id": self.audit_id,
            "client_name": self.client_name,
            "batch_name": self.batch_name,
            "source_dir": self.source_dir,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status.value,
            "config": self.config.to_dict() if self.config else None,
            "results": [r.to_dict() for r in self.results],
            "exported_paths": self.exported_paths,
            "log_entries": self.log_entries,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "AcceptanceAuditRecord":
        return cls(
            audit_id=data["audit_id"],
            client_name=data["client_name"],
            batch_name=data["batch_name"],
            source_dir=data["source_dir"],
            started_at=datetime.fromisoformat(data["started_at"]),
            updated_at=datetime.fromisoformat(data.get("updated_at", data["started_at"])),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            status=AcceptanceCheckStatus(data.get("status", AcceptanceCheckStatus.PENDING.value)),
            config=AcceptanceAuditConfig.from_dict(data["config"]) if data.get("config") else None,
            results=[AcceptanceCheckResult.from_dict(r) for r in data.get("results", [])],
            exported_paths=data.get("exported_paths", {}),
            log_entries=data.get("log_entries", []),
            error_message=data.get("error_message"),
        )

    def get_statistics(self) -> Dict:
        pass_count = sum(1 for r in self.results if r.status == AcceptanceCheckStatus.PASS)
        fail_count = sum(1 for r in self.results if r.status == AcceptanceCheckStatus.FAIL)
        warning_count = sum(1 for r in self.results if r.status == AcceptanceCheckStatus.WARNING)
        return {
            "total_rules": len(self.results),
            "passed": pass_count,
            "failed": fail_count,
            "warnings": warning_count,
            "overall": "PASS" if fail_count == 0 else "FAIL",
        }


class AcceptanceAuditError(Exception):
    """验收审计基础异常"""

    def __init__(self, message: str, error_code: int = 1):
        super().__init__(message)
        self.error_code = error_code


class AcceptanceConfigError(AcceptanceAuditError):
    """验收配置错误"""

    def __init__(self, message: str, missing_fields: Optional[List[str]] = None):
        super().__init__(message, error_code=15)
        self.missing_fields = missing_fields or []

    def to_dict(self) -> Dict:
        return {
            "error": "acceptance_config_error",
            "message": str(self),
            "missing_fields": self.missing_fields,
        }


class AcceptanceRuleConflictError(AcceptanceAuditError):
    """验收规则冲突"""

    def __init__(self, message: str, conflicting_rules: Optional[List[str]] = None):
        super().__init__(message, error_code=16)
        self.conflicting_rules = conflicting_rules or []

    def to_dict(self) -> Dict:
        return {
            "error": "acceptance_rule_conflict",
            "message": str(self),
            "conflicting_rules": self.conflicting_rules,
        }


class AcceptanceDirectoryNotFoundError(AcceptanceAuditError):
    """待检查目录不存在"""

    def __init__(self, directory_path: str):
        super().__init__(f"待检查目录不存在: {directory_path}", error_code=17)
        self.directory_path = directory_path

    def to_dict(self) -> Dict:
        return {
            "error": "acceptance_directory_not_found",
            "message": str(self),
            "directory_path": self.directory_path,
        }


class AcceptanceExportExistsError(AcceptanceAuditError):
    """导出文件已存在"""

    def __init__(self, export_path: str):
        super().__init__(f"导出文件已存在: {export_path}，请删除或使用其他文件名", error_code=18)
        self.export_path = export_path

    def to_dict(self) -> Dict:
        return {
            "error": "acceptance_export_exists",
            "message": str(self),
            "export_path": self.export_path,
        }


class AcceptanceWritePermissionError(AcceptanceAuditError):
    """只读目录写入失败"""

    def __init__(self, directory_path: str, export_path: str):
        super().__init__(f"无法写入导出文件: {export_path}，目录无写入权限: {directory_path}", error_code=19)
        self.directory_path = directory_path
        self.export_path = export_path

    def to_dict(self) -> Dict:
        return {
            "error": "acceptance_write_permission_error",
            "message": str(self),
            "directory_path": self.directory_path,
            "export_path": self.export_path,
        }
