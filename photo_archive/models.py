from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
import json
import uuid


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
    applied: bool = False
    applied_at: Optional[datetime] = None
    rolled_back: bool = False

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "source": self.source,
            "target": self.target,
            "reason": self.reason,
            "applied": self.applied,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "rolled_back": self.rolled_back,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "CorrectionAction":
        return cls(
            id=data["id"],
            type=CorrectionType(data["type"]),
            source=data["source"],
            target=data["target"],
            reason=data["reason"],
            applied=data.get("applied", False),
            applied_at=datetime.fromisoformat(data["applied_at"]) if data.get("applied_at") else None,
            rolled_back=data.get("rolled_back", False),
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
    scan_source_dir: Optional[str] = None
    last_scan_at: Optional[datetime] = None
    last_import_at: Optional[datetime] = None
    last_verify_at: Optional[datetime] = None

    def to_dict(self) -> Dict:
        return {
            "batch_id": self.batch_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "scanned_files": {k: v.to_dict() for k, v in self.scanned_files.items()},
            "delivery_list": {k: v.to_dict() for k, v in self.delivery_list.items()},
            "corrections": [c.to_dict() for c in self.corrections],
            "scan_source_dir": self.scan_source_dir,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "last_import_at": self.last_import_at.isoformat() if self.last_import_at else None,
            "last_verify_at": self.last_verify_at.isoformat() if self.last_verify_at else None,
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
            scan_source_dir=data.get("scan_source_dir"),
            last_scan_at=datetime.fromisoformat(data["last_scan_at"]) if data.get("last_scan_at") else None,
            last_import_at=datetime.fromisoformat(data["last_import_at"]) if data.get("last_import_at") else None,
            last_verify_at=datetime.fromisoformat(data["last_verify_at"]) if data.get("last_verify_at") else None,
        )
