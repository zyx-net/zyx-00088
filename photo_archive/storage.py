from pathlib import Path
from typing import Dict, List, Optional
import json

from .models import BatchHistory, Profile, ProfileNameConflictError, AuditLogEntry


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

    def find_batches_by_normalized_name(self, normalized_name: str) -> List[BatchHistory]:
        """查找归一化名称匹配的所有批次"""
        batches = self.list_batches()
        result = []
        for entry in batches:
            batch = self.load(entry["batch_id"])
            if batch and batch.normalized_name == normalized_name:
                result.append(batch)
        return result


class ProfileStorage:
    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.profiles_dir = self.work_dir / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.profiles_dir / "index.json"
        self.audit_log_file = self.profiles_dir / "audit_log.json"

    def _get_profile_path(self, profile_name: str) -> Path:
        safe_name = "".join(c for c in profile_name if c.isalnum() or c in ("-", "_", " ")).strip()
        return self.profiles_dir / f"{safe_name}.json"

    def save(self, profile: Profile) -> None:
        from datetime import datetime
        profile.updated_at = datetime.now()
        profile_path = self._get_profile_path(profile.name)
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(profile)
        self._log_audit("save", profile.name, {"version": profile.version})

    def load(self, profile_name: str) -> Optional[Profile]:
        profile_path = self._get_profile_path(profile_name)
        if not profile_path.exists():
            return None
        with open(profile_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = Profile.from_dict(data)
        self._log_audit("load", profile_name, {"version": profile.version})
        return profile

    def list_profiles(self) -> List[Dict]:
        if not self.index_file.exists():
            return []
        with open(self.index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _update_index(self, profile: Profile) -> None:
        profiles = self.list_profiles()
        existing = next((p for p in profiles if p["name"] == profile.name), None)
        entry = {
            "name": profile.name,
            "description": profile.description,
            "version": profile.version,
            "created_at": profile.created_at.isoformat(),
            "updated_at": profile.updated_at.isoformat(),
            "output_format": profile.output_format,
            "conflict_strategy": profile.conflict_strategy,
        }
        if existing:
            existing.update(entry)
        else:
            profiles.append(entry)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)

    def delete(self, profile_name: str) -> bool:
        profile_path = self._get_profile_path(profile_name)
        if profile_path.exists():
            profile_path.unlink()
            profiles = [p for p in self.list_profiles() if p["name"] != profile_name]
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(profiles, f, ensure_ascii=False, indent=2)
            self._log_audit("delete", profile_name)
            return True
        self._log_audit("delete", profile_name, success=False, error_message="Profile not found")
        return False

    def exists(self, profile_name: str) -> bool:
        return self._get_profile_path(profile_name).exists()

    def export_profile(self, profile_name: str, export_path: Path) -> bool:
        profile = self.load(profile_name)
        if not profile:
            self._log_audit("export", profile_name, success=False, error_message="Profile not found")
            return False
        export_path = Path(export_path)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
        self._log_audit("export", profile_name, {"export_path": str(export_path)})
        return True

    def import_profile(self, import_path: Path, overwrite: bool = False, rename: Optional[str] = None) -> Profile:
        import_path = Path(import_path)
        if not import_path.exists():
            raise FileNotFoundError(f"导入文件不存在: {import_path}")
        with open(import_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = Profile.from_dict(data)
        if rename:
            profile.name = rename
        if self.exists(profile.name) and not overwrite:
            self._log_audit(
                "import", profile.name,
                success=False,
                error_message="Profile name already exists"
            )
            raise ProfileNameConflictError(profile.name, self.load(profile.name))
        self.save(profile)
        self._log_audit("import", profile.name, {"import_path": str(import_path)})
        return profile

    def _log_audit(self, operation: str, profile_name: Optional[str], details: Dict = None, success: bool = True, error_message: Optional[str] = None) -> None:
        import uuid as _uuid
        from datetime import datetime as _dt
        entry = AuditLogEntry(
            entry_id=str(_uuid.uuid4())[:8],
            timestamp=_dt.now(),
            operation=operation,
            profile_name=profile_name,
            details=details or {},
            success=success,
            error_message=error_message,
        )
        self._append_audit_log(entry)

    def _append_audit_log(self, entry: AuditLogEntry) -> None:
        logs = self._load_audit_log()
        logs.append(entry.to_dict())
        with open(self.audit_log_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)

    def _load_audit_log(self) -> List[Dict]:
        if not self.audit_log_file.exists():
            return []
        with open(self.audit_log_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_audit_log(self, limit: int = 100) -> List[AuditLogEntry]:
        logs = self._load_audit_log()
        entries = [AuditLogEntry.from_dict(d) for d in logs]
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]
