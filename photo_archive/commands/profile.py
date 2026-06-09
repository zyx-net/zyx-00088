from typing import Dict, Optional
from pathlib import Path

from ..config import Config
from ..models import Profile, ProfileNameConflictError
from ..storage import ProfileStorage


def save_profile(
    config: Config,
    profile_storage: ProfileStorage,
    name: str,
    description: str = "",
    conflict_strategy: str = "fail",
    resume: bool = True,
    skip_conflicts: bool = False,
    output_format: str = "text",
    log_level: str = "INFO",
    default_limit: Optional[int] = None,
    overwrite: bool = False,
) -> Dict:
    if profile_storage.exists(name) and not overwrite:
        raise ProfileNameConflictError(name, profile_storage.load(name))

    profile = Profile.from_config(name, config, description)
    profile.conflict_strategy = conflict_strategy
    profile.resume = resume
    profile.skip_conflicts = skip_conflicts
    profile.output_format = output_format
    profile.log_level = log_level
    profile.default_limit = default_limit

    profile_storage.save(profile)

    return {
        "profile_name": profile.name,
        "operation": "saved" if overwrite else "created",
        "profile": profile.to_dict(),
    }


def load_profile(
    profile_storage: ProfileStorage,
    name: str,
) -> Dict:
    profile = profile_storage.load(name)
    if not profile:
        raise ValueError(f"找不到 Profile: {name}")

    return {
        "profile_name": profile.name,
        "profile": profile.to_dict(),
    }


def list_profiles(
    profile_storage: ProfileStorage,
) -> Dict:
    profiles = profile_storage.list_profiles()
    return {
        "count": len(profiles),
        "profiles": profiles,
    }


def delete_profile(
    profile_storage: ProfileStorage,
    name: str,
) -> Dict:
    success = profile_storage.delete(name)
    if not success:
        raise ValueError(f"找不到 Profile: {name}")
    return {
        "profile_name": name,
        "operation": "deleted",
    }


def export_profile(
    profile_storage: ProfileStorage,
    name: str,
    output_path: str,
) -> Dict:
    success = profile_storage.export_profile(name, Path(output_path))
    if not success:
        raise ValueError(f"找不到 Profile: {name}")
    return {
        "profile_name": name,
        "export_path": str(output_path),
        "operation": "exported",
    }


def import_profile(
    profile_storage: ProfileStorage,
    import_path: str,
    overwrite: bool = False,
    rename: Optional[str] = None,
) -> Dict:
    import_file = Path(import_path)
    if not import_file.exists():
        raise FileNotFoundError(f"导入文件不存在: {import_path}")

    profile = profile_storage.import_profile(import_file, overwrite, rename)

    return {
        "profile_name": profile.name,
        "operation": "imported",
        "profile": profile.to_dict(),
    }


def get_audit_log(
    profile_storage: ProfileStorage,
    limit: int = 100,
) -> Dict:
    entries = profile_storage.get_audit_log(limit)
    return {
        "count": len(entries),
        "entries": [e.to_dict() for e in entries],
    }


def apply_profile_to_config(
    profile: Profile,
    config: Config,
) -> Config:
    profile.apply_to_config(config)
    return config


def merge_profile_with_cli_args(
    profile: Optional[Profile],
    cli_args: Dict,
) -> Dict:
    defaults = {
        "resume": True,
        "skip_conflicts": False,
        "limit": None,
        "output_json": False,
        "log_level": "INFO",
    }

    if not profile:
        result = {}
        for k, v in defaults.items():
            result[k] = cli_args.get(k) if cli_args.get(k) is not None else v
        return result

    result = {
        "resume": cli_args["resume"] if cli_args.get("resume") is not None else profile.resume,
        "skip_conflicts": cli_args["skip_conflicts"] if cli_args.get("skip_conflicts") is not None else profile.skip_conflicts,
        "limit": cli_args["limit"] if cli_args.get("limit") is not None else profile.default_limit,
        "output_json": cli_args["output_json"] if cli_args.get("output_json") is not None else (profile.output_format == "json"),
        "log_level": profile.log_level,
    }

    return result
