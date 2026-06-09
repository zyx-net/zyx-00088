from pathlib import Path
import subprocess
import sys
import shutil
import os
import json
import time
import tempfile

import pytest


TEST_DIR = Path(__file__).parent.parent.resolve()
SAMPLE_DIR = TEST_DIR / "test_profile_data"
WORK_DIR = TEST_DIR / "test_profile_work"
ARCHIVE_DIR = TEST_DIR / "test_profile_archive"


def run_photo_archive(args, cwd=None, check=True):
    cmd = [sys.executable, "-m", "photo_archive.cli"] + args
    env = os.environ.copy()
    if cwd:
        env["PYTHONPATH"] = str(TEST_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd,
        cwd=cwd or TEST_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.stdout is None:
        result.stdout = ""
    if result.stderr is None:
        result.stderr = ""
    return result


@pytest.fixture(autouse=True)
def setup_and_teardown():
    default_work = TEST_DIR / "work"
    default_archive = TEST_DIR / "archive"
    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, default_work, default_archive]:
        if d.exists():
            shutil.rmtree(d)
    yield
    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, default_work, default_archive]:
        if d.exists():
            shutil.rmtree(d)


def create_config(config_path, work_dir, archive_dir, cameras=None):
    with open(config_path, "w", encoding="utf-8") as f:
        f.write('naming_rule: "{机位}_{批次}_{序号:04d}.{扩展名}"\n')
        f.write("cameras:\n")
        for cam in (cameras or ["A", "B"]):
            f.write(f"  - {cam}\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {archive_dir}\n")
        f.write(f"work_dir: {work_dir}\n")


def create_source_file(card_dir, filename, content):
    card_dir.mkdir(parents=True, exist_ok=True)
    file_path = card_dir / filename
    with open(file_path, "wb") as f:
        f.write(content)
    return file_path


def get_file_hash(file_path):
    import hashlib
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def test_profile_crud_operations():
    """测试 Profile 的基本 CRUD 操作"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "test-profile",
        "--description", "测试 Profile",
        "--conflict-strategy", "skip",
        "--output-format", "json",
        "--log-level", "DEBUG",
        "--default-limit", "10",
        "--json",
    ])
    assert result.returncode == 0, f"STDOUT: {result.stdout}, STDERR: {result.stderr}"
    save_data = json.loads(result.stdout)
    assert save_data["profile_name"] == "test-profile"
    assert save_data["operation"] == "created"
    assert save_data["profile"]["conflict_strategy"] == "skip"
    assert save_data["profile"]["output_format"] == "json"
    assert save_data["profile"]["log_level"] == "DEBUG"
    assert save_data["profile"]["default_limit"] == 10

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
        "--json",
    ])
    assert result.returncode == 0
    list_data = json.loads(result.stdout)
    assert list_data["count"] == 1
    assert list_data["profiles"][0]["name"] == "test-profile"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "test-profile",
        "--json",
    ])
    assert result.returncode == 0
    load_data = json.loads(result.stdout)
    assert load_data["profile_name"] == "test-profile"
    assert load_data["profile"]["description"] == "测试 Profile"
    assert load_data["profile"]["conflict_strategy"] == "skip"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "test-profile",
        "--description", "更新后的 Profile",
        "--overwrite",
        "--json",
    ])
    assert result.returncode == 0
    update_data = json.loads(result.stdout)
    assert update_data["operation"] == "saved"
    assert update_data["profile"]["description"] == "更新后的 Profile"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "delete", "test-profile",
        "--json",
    ])
    assert result.returncode == 0
    delete_data = json.loads(result.stdout)
    assert delete_data["profile_name"] == "test-profile"
    assert delete_data["operation"] == "deleted"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
        "--json",
    ])
    assert result.returncode == 0
    list_data = json.loads(result.stdout)
    assert list_data["count"] == 0


def test_profile_name_conflict():
    """测试 Profile 名称冲突处理"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "conflict-profile",
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "conflict-profile",
        "--json",
    ])
    assert result.returncode == 10
    conflict_data = json.loads(result.stdout)
    assert conflict_data["error"] == "profile_name_conflict"
    assert conflict_data["profile_name"] == "conflict-profile"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "conflict-profile",
        "--overwrite",
        "--json",
    ])
    assert result.returncode == 0


def test_profile_import_export():
    """测试 Profile 导入导出功能"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "export-profile",
        "--description", "用于导出测试",
        "--conflict-strategy", "fail",
        "--output-format", "text",
        "--default-limit", "5",
        "--json",
    ])
    assert result.returncode == 0

    export_file = SAMPLE_DIR / "exported_profile.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "export", "export-profile", str(export_file),
        "--json",
    ])
    assert result.returncode == 0
    assert export_file.exists()

    with open(export_file, "r", encoding="utf-8") as f:
        exported_data = json.load(f)
    assert exported_data["name"] == "export-profile"
    assert exported_data["description"] == "用于导出测试"
    assert exported_data["default_limit"] == 5

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "delete", "export-profile",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "import", str(export_file),
        "--json",
    ])
    assert result.returncode == 0
    import_data = json.loads(result.stdout)
    assert import_data["profile_name"] == "export-profile"
    assert import_data["operation"] == "imported"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "export-profile",
        "--json",
    ])
    assert result.returncode == 0
    load_data = json.loads(result.stdout)
    assert load_data["profile"]["description"] == "用于导出测试"
    assert load_data["profile"]["default_limit"] == 5


def test_profile_import_conflict():
    """测试导入同名 Profile 冲突处理"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "import-conflict",
        "--description", "原始 Profile",
        "--json",
    ])
    assert result.returncode == 0

    export_file = SAMPLE_DIR / "import_test.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "export", "import-conflict", str(export_file),
    ])
    assert result.returncode == 0

    with open(export_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["description"] = "修改后的 Profile"
    with open(export_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "import", str(export_file),
        "--json",
    ])
    assert result.returncode == 10

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "import", str(export_file),
        "--rename", "renamed-profile",
        "--json",
    ])
    assert result.returncode == 0
    rename_data = json.loads(result.stdout)
    assert rename_data["profile_name"] == "renamed-profile"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "import", str(export_file),
        "--overwrite",
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "import-conflict",
        "--json",
    ])
    assert result.returncode == 0
    load_data = json.loads(result.stdout)
    assert load_data["profile"]["description"] == "修改后的 Profile"


def test_profile_version_upgrade():
    """测试 Profile 版本升级（旧格式自动迁移）"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    profiles_dir = WORK_DIR / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    old_profile = {
        "profile_version": 1,
        "name": "old-version-profile",
        "description": "旧版本 Profile",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
        "naming_rule": "{机位}_{批次}_{序号:04d}.{扩展名}",
        "cameras": ["A", "B"],
        "hash_strategy": "sha256",
        "archive_dir": "./archive",
        "work_dir": "./work",
    }

    profile_file = profiles_dir / "old-version-profile.json"
    with open(profile_file, "w", encoding="utf-8") as f:
        json.dump(old_profile, f)

    index_file = profiles_dir / "index.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump([{
            "name": "old-version-profile",
            "description": "旧版本 Profile",
            "version": 1,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }], f)

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "old-version-profile",
        "--json",
    ])
    assert result.returncode == 0
    load_data = json.loads(result.stdout)
    assert load_data["profile"]["version"] == 2
    assert load_data["profile"]["conflict_strategy"] == "fail"
    assert load_data["profile"]["resume"] == True
    assert load_data["profile"]["skip_conflicts"] == False
    assert load_data["profile"]["output_format"] == "text"
    assert load_data["profile"]["log_level"] == "INFO"
    assert load_data["profile"]["default_limit"] is None


def test_profile_cli_args_override():
    """测试命令行参数覆盖 Profile 配置"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    f1 = create_source_file(source_dir, "IMG_0001.jpg", b"photo1content")
    f2 = create_source_file(source_dir, "IMG_0002.jpg", b"photo2content")
    hash1 = get_file_hash(f1)
    hash2 = get_file_hash(f2)

    manifest_dir = SAMPLE_DIR / "delivery_list"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / "m1.csv"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_OVERRIDE_0001.jpg,{hash1},A,1\n")
        f.write(f"A_OVERRIDE_0002.jpg,{hash2},A,2\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "override-profile",
        "--output-format", "json",
        "--skip-conflicts",
        "--default-limit", "1",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "override-test",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest),
        "--batch-id", batch_id,
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--batch-id", batch_id,
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--profile", "override-profile",
    ])
    assert result.returncode == 0
    assert "{" in result.stdout
    plan_data = json.loads(result.stdout)
    assert plan_data["statistics"]["pending"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--profile", "override-profile",
    ])
    assert result.returncode == 0
    apply_data = json.loads(result.stdout)
    assert apply_data["applied_count"] == 1
    assert apply_data["limit"] == 1

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--profile", "override-profile",
        "--limit", "2",
        "--no-resume",
    ])
    assert result.returncode == 0
    apply_data2 = json.loads(result.stdout)
    assert apply_data2["limit"] == 2
    assert apply_data2["skipped_count"] == 0
    assert apply_data2["applied_count"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--profile", "override-profile",
    ])
    assert result.returncode == 0
    assert "{" in result.stdout
    status_data = json.loads(result.stdout)
    assert status_data["completed_count"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--batch-id", batch_id,
        "--profile", "override-profile",
    ])
    assert result.returncode == 0
    assert "{" in result.stdout
    undo_data = json.loads(result.stdout)
    assert undo_data["undone_count"] == 2


def test_profile_persistence_across_process_restart():
    """测试跨进程重启后 Profile 仍然可用"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    f1 = create_source_file(source_dir, "IMG_0001.jpg", b"photo1content")
    f2 = create_source_file(source_dir, "IMG_0002.jpg", b"photo2content")
    f3 = create_source_file(source_dir, "IMG_0003.jpg", b"photo3content")
    f4 = create_source_file(source_dir, "IMG_0004.jpg", b"photo4content")
    hash1 = get_file_hash(f1)
    hash2 = get_file_hash(f2)
    hash3 = get_file_hash(f3)
    hash4 = get_file_hash(f4)

    manifest_dir = SAMPLE_DIR / "delivery_list"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / "m1.csv"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_PERSIST_0001.jpg,{hash1},A,1\n")
        f.write(f"A_PERSIST_0002.jpg,{hash2},A,2\n")
        f.write(f"A_PERSIST_0003.jpg,{hash3},A,3\n")
        f.write(f"A_PERSIST_0004.jpg,{hash4},A,4\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "persist-profile",
        "--description", "持久化测试 Profile",
        "--output-format", "json",
        "--default-limit", "2",
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "persist-test",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest),
        "--batch-id", batch_id,
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--batch-id", batch_id,
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--profile", "persist-profile",
    ])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)
    assert plan_data["statistics"]["pending"] == 4

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--profile", "persist-profile",
    ])
    assert result.returncode == 0
    apply1 = json.loads(result.stdout)
    assert apply1["applied_count"] == 2
    assert apply1["remaining_count"] == 2

    archive_dir = ARCHIVE_DIR / "persist-test"
    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
        "--json",
    ])
    assert result.returncode == 0
    list_data = json.loads(result.stdout)
    assert list_data["count"] == 1
    assert list_data["profiles"][0]["name"] == "persist-profile"

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--profile", "persist-profile",
    ])
    assert result.returncode == 0
    apply2 = json.loads(result.stdout)
    assert apply2["applied_count"] == 2
    assert apply2["remaining_count"] == 0

    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 4

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--profile", "persist-profile",
    ])
    assert result.returncode == 0
    status_data = json.loads(result.stdout)
    assert status_data["completed_count"] == 4
    assert status_data["progress_percent"] == 100

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--batch-id", batch_id,
        "--profile", "persist-profile",
    ])
    assert result.returncode == 0
    undo_data = json.loads(result.stdout)
    assert undo_data["undone_count"] == 4

    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 0


def test_profile_output_format_consistency():
    """测试 JSON 和文本输出格式一致性"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "json-profile",
        "--output-format", "json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "text-profile",
        "--output-format", "text",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "json-profile",
        "--json",
    ])
    assert result.returncode == 0
    json_output = json.loads(result.stdout)
    assert json_output["profile"]["output_format"] == "json"

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "json-profile",
    ])
    assert result.returncode == 0
    assert "Profile: json-profile" in result.stdout
    assert "输出格式: json" in result.stdout

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
        "--json",
    ])
    assert result.returncode == 0
    list_json = json.loads(result.stdout)
    assert list_json["count"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
    ])
    assert result.returncode == 0
    assert "共 2 个 Profile" in result.stdout
    assert "json-profile" in result.stdout
    assert "text-profile" in result.stdout


def test_profile_audit_logging():
    """测试 Profile 操作审计日志"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "audit-profile",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "audit-profile",
    ])
    assert result.returncode == 0

    export_file = SAMPLE_DIR / "audit_export.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "export", "audit-profile", str(export_file),
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "delete", "audit-profile",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "import", str(export_file),
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "audit-log",
        "--json",
    ])
    assert result.returncode == 0
    audit_data = json.loads(result.stdout)
    assert audit_data["count"] >= 5

    operations = [e["operation"] for e in audit_data["entries"]]
    assert "save" in operations
    assert "load" in operations
    assert "export" in operations
    assert "delete" in operations
    assert "import" in operations

    save_entries = [e for e in audit_data["entries"] if e["operation"] == "save"]
    assert len(save_entries) >= 1
    assert save_entries[0]["success"] == True
    assert save_entries[0]["profile_name"] == "audit-profile"


def test_profile_missing_config_file():
    """测试配置文件缺失时使用默认值创建 Profile"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    missing_config_path = SAMPLE_DIR / "nonexistent_config.yaml"

    result = run_photo_archive([
        "-c", str(missing_config_path),
        "profile", "save", "default-profile",
        "--json",
    ])
    assert result.returncode == 0
    save_data = json.loads(result.stdout)
    assert save_data["profile"]["naming_rule"] == "{机位}_{批次}_{序号:04d}.{扩展名}"
    assert save_data["profile"]["cameras"] == ["A", "B", "C"]
    assert save_data["profile"]["hash_strategy"] == "sha256"


def test_profile_import_missing_file():
    """测试导入不存在的文件时的错误处理"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    nonexistent_file = SAMPLE_DIR / "nonexistent.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "import", str(nonexistent_file),
        "--json",
    ])
    assert result.returncode == 1
    error_data = json.loads(result.stdout)
    assert "error" in error_data
    assert "不存在" in error_data["error"]


def test_profile_load_nonexistent():
    """测试加载不存在的 Profile 时的错误处理"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "nonexistent-profile",
        "--json",
    ])
    assert result.returncode == 1
    error_data = json.loads(result.stdout)
    assert "error" in error_data
    assert "找不到" in error_data["error"]


def test_profile_apply_undo_state_preservation():
    """测试导入导出 Profile 后再执行 apply/undo 不丢失状态"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    f1 = create_source_file(source_dir, "IMG_0001.jpg", b"state1content")
    f2 = create_source_file(source_dir, "IMG_0002.jpg", b"state2content")
    hash1 = get_file_hash(f1)
    hash2 = get_file_hash(f2)

    manifest_dir = SAMPLE_DIR / "delivery_list"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / "m1.csv"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_STATE_0001.jpg,{hash1},A,1\n")
        f.write(f"A_STATE_0002.jpg,{hash2},A,2\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "state-profile",
        "--description", "状态保存测试",
        "--output-format", "json",
        "--json",
    ])
    assert result.returncode == 0

    export_file = SAMPLE_DIR / "state_profile.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "export", "state-profile", str(export_file),
    ])
    assert result.returncode == 0

    profiles_dir = WORK_DIR / "profiles"
    if profiles_dir.exists():
        shutil.rmtree(profiles_dir)

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
        "--json",
    ])
    assert result.returncode == 0
    list_data = json.loads(result.stdout)
    assert list_data["count"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "import", str(export_file),
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "state-profile",
        "--json",
    ])
    assert result.returncode == 0
    load_data = json.loads(result.stdout)
    assert load_data["profile"]["description"] == "状态保存测试"
    assert load_data["profile"]["output_format"] == "json"

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "state-test",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest),
        "--batch-id", batch_id,
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--batch-id", batch_id,
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--profile", "state-profile",
    ])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)
    assert plan_data["statistics"]["pending"] == 2
    assert plan_data["statistics"]["completed"] == 0
    assert plan_data["statistics"]["undoable"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--profile", "state-profile",
    ])
    assert result.returncode == 0
    apply_data = json.loads(result.stdout)
    assert apply_data["applied_count"] == 2
    assert apply_data["statistics"]["completed"] == 2
    assert apply_data["statistics"]["undoable"] == 2

    archive_dir = ARCHIVE_DIR / "state-test"
    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--profile", "state-profile",
    ])
    assert result.returncode == 0
    status_data = json.loads(result.stdout)
    assert status_data["completed_count"] == 2
    assert status_data["undoable_count"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--batch-id", batch_id,
        "--profile", "state-profile",
    ])
    assert result.returncode == 0
    undo_data = json.loads(result.stdout)
    assert undo_data["undone_count"] == 2
    assert undo_data["statistics"]["rolled_back"] == 2
    assert undo_data["statistics"]["undoable"] == 0
    assert undo_data["statistics"]["completed"] == 0

    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--profile", "state-profile",
    ])
    assert result.returncode == 0
    status2 = json.loads(result.stdout)
    assert status2["rolled_back_count"] == 2
    assert status2["completed_count"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--profile", "state-profile",
    ])
    assert result.returncode == 0
    plan2 = json.loads(result.stdout)
    assert plan2["statistics"]["pending"] == 2
    assert plan2["statistics"]["rolled_back"] == 2
    assert plan2["statistics"]["completed"] == 0


def test_profile_text_output_format():
    """测试 Profile 相关命令的文本输出格式"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    config_path = SAMPLE_DIR / "config.yaml"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "save", "text-format-test",
        "--description", "文本格式测试",
        "--conflict-strategy", "skip",
        "--output-format", "text",
        "--log-level", "WARNING",
    ])
    assert result.returncode == 0
    assert "Profile 已created" in result.stdout or "Profile 已创建" in result.stdout or "[OK] Profile 已" in result.stdout
    assert "冲突策略: skip" in result.stdout
    assert "输出格式: text" in result.stdout
    assert "日志级别: WARNING" in result.stdout

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
    ])
    assert result.returncode == 0
    assert "共 1 个 Profile" in result.stdout
    assert "text-format-test" in result.stdout

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "load", "text-format-test",
    ])
    assert result.returncode == 0
    assert "[OK] Profile: text-format-test" in result.stdout
    assert "描述: 文本格式测试" in result.stdout
    assert "归档规则:" in result.stdout
    assert "运行策略:" in result.stdout
    assert "输出配置:" in result.stdout

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "delete", "text-format-test",
    ])
    assert result.returncode == 0
    assert "[OK] Profile 已删除" in result.stdout

    result = run_photo_archive([
        "-c", str(config_path),
        "profile", "list",
    ])
    assert result.returncode == 0
    assert "(没有保存的 Profile)" in result.stdout


def setup_package_test_environment(sample_dir, config_path, source_dir, manifest_path, batch_name="PKGTEST"):
    """设置打包测试环境：初始化样例、扫描、导入清单、校验、计划、应用"""
    work_dir = sample_dir / "work"
    archive_dir = sample_dir / "archive"
    
    run_photo_archive([
        "init-sample", str(sample_dir),
        "--cameras", "2",
        "--photos", "3",
        "--batch", batch_name,
    ])

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {archive_dir}\n")
        f.write(f"work_dir: {work_dir}\n")

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write(f"A_{batch_name}_0001.jpg,A,1\n")
        f.write(f"A_{batch_name}_0002.jpg,A,2\n")
        f.write(f"A_{batch_name}_0003.jpg,A,3\n")
        f.write(f"B_{batch_name}_0001.jpg,B,1\n")
        f.write(f"B_{batch_name}_0002.jpg,B,2\n")

    card_a_dir = source_dir / "card_A"
    for i, f in enumerate(sorted(card_a_dir.glob("*.jpg"))):
        new_name = f"A_{batch_name}_{i+1:04d}.jpg"
        f.rename(card_a_dir / new_name)

    card_b_dir = source_dir / "card_B"
    for i, f in enumerate(sorted(card_b_dir.glob("*.jpg"))):
        new_name = f"B_{batch_name}_{i+1:04d}.jpg"
        f.rename(card_b_dir / new_name)

    duplicate_dir = source_dir / "card_A_duplicate"
    if duplicate_dir.exists():
        shutil.rmtree(duplicate_dir)

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", f"{batch_name.lower()}-batch",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest_path),
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
    ])
    assert result.returncode == 0


def test_package_dry_run():
    """测试 dry-run 预览功能"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_dryrun"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "DRYRUN")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--dry-run",
            "--json",
        ])
        assert result.returncode == 0, f"dry-run failed: {result.stderr}"

        data = json.loads(result.stdout)
        assert data["dry_run"] is True
        assert data["status"] == "dry_run"
        assert data["total_files"] >= 5
        assert data["total_size"] > 0
        assert data["copied_files"] == 0
        assert len(data["items"]) >= 5
        assert "package_structure" in data
        assert "photos" in data["package_structure"]

        assert not DELIVERY_DIR.exists(), "dry-run 不应创建实际目录"

        result_text = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--dry-run",
        ])
        assert result_text.returncode == 0
        assert "[PREVIEW]" in result_text.stdout
        assert "总计文件" in result_text.stdout
        assert "总计大小" in result_text.stdout
        assert "将复制的文件" in result_text.stdout

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_real_packaging():
    """测试真实打包功能"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_real"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, DELIVERY_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "REALPKG")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--notes", "测试交付包",
            "--json",
        ])
        assert result.returncode == 0, f"package create failed: {result.stderr}"

        data = json.loads(result.stdout)
        assert data["status"] == "completed"
        assert data["dry_run"] is False
        assert data["total_files"] >= 5
        assert data["copied_files"] >= 5
        assert data["total_size"] > 0
        assert data["copied_size"] > 0

        assert DELIVERY_DIR.exists()

        photos_dir = DELIVERY_DIR / "photos"
        assert photos_dir.exists()

        photo_files = list(photos_dir.glob("*.jpg"))
        assert len(photo_files) >= 5

        manifest_path = DELIVERY_DIR / "manifest.json"
        assert manifest_path.exists()
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest["package_id"] == data["package_id"]
        assert manifest["batch_name"] == "realpkg-batch"
        assert len(manifest["files"]) >= 5
        for file_info in manifest["files"]:
            assert "file_name" in file_info
            assert "hash" in file_info
            assert "size" in file_info

        checksum_path = DELIVERY_DIR / "checksums.sha256"
        assert checksum_path.exists()
        checksum_content = checksum_path.read_text(encoding="utf-8")
        assert len(checksum_content.strip().split("\n")) >= 5

        readme_path = DELIVERY_DIR / "交付说明.txt"
        assert readme_path.exists()
        readme_content = readme_path.read_text(encoding="utf-8")
        assert "照片交付说明" in readme_content
        assert "批次名称: realpkg-batch" in readme_content
        assert "测试交付包" in readme_content
        assert "目录结构:" in readme_content
        assert "校验方法:" in readme_content

        for item in data["items"]:
            target_path = Path(item["target_path"])
            assert target_path.exists(), f"文件应该存在: {target_path}"

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_target_exists_error():
    """测试目标目录已存在时的错误处理（退出码 11）"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_exists"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, DELIVERY_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "EXISTPKG")

        DELIVERY_DIR.mkdir(parents=True, exist_ok=True)
        test_file = DELIVERY_DIR / "existing_file.txt"
        test_file.write_text("existing")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--json",
        ])
        assert result.returncode == 11, f"应该返回退出码 11，实际为 {result.returncode}: {result.stderr}"

        data = json.loads(result.stdout)
        assert data["error"] == "package_target_exists"
        assert "目标目录已存在" in data["message"]

        result_text = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
        ])
        assert result_text.returncode == 11
        assert "[FAIL]" in result_text.stderr
        assert "目标目录已存在" in result_text.stderr

        result_force = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--force",
            "--json",
        ])
        assert result_force.returncode == 0, f"force should succeed: {result_force.stderr}"

        assert test_file.exists(), "原有不相关的文件应该被保留（不会静默删除）"
        force_data = json.loads(result_force.stdout)
        assert force_data["status"] == "completed"
        assert force_data["copied_files"] == 5

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_file_conflict_error():
    """测试同名文件冲突时的错误处理（退出码 12）"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_conflict"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, DELIVERY_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "CONFPKG")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--json",
        ])
        assert result.returncode == 0, f"first package should succeed: {result.stderr}"

        photos_dir = DELIVERY_DIR / "photos"
        first_jpg = list(photos_dir.glob("*.jpg"))[0]
        with open(first_jpg, "ab") as f:
            f.write(b"modified content to create conflict")

        result_conflict = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--force",
            "--json",
        ])
        assert result_conflict.returncode == 12, f"应该返回退出码 12，实际为 {result_conflict.returncode}: {result_conflict.stderr}"

        data = json.loads(result_conflict.stdout)
        assert data["error"] == "package_file_conflict"
        assert "内容不匹配" in data["message"]

        result_skip = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--force",
            "--skip-conflicts",
            "--json",
        ])
        assert result_skip.returncode == 0, f"skip-conflicts should succeed: {result_skip.stderr}"

        data_skip = json.loads(result_skip.stdout)
        assert data_skip["skipped_files"] >= 1

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_source_modified_error():
    """测试源文件被篡改时的错误处理（退出码 14）"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_modified"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, DELIVERY_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "MODPKG")

        card_a_dir = source_dir / "card_A"
        first_file = list(card_a_dir.glob("*.jpg"))[0]
        with open(first_file, "ab") as f:
            f.write(b"tampered content")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--json",
        ])
        assert result.returncode == 14, f"应该返回退出码 14，实际为 {result.returncode}: {result.stderr}"

        data = json.loads(result.stdout)
        assert data["error"] == "package_source_modified"
        assert "源文件已被篡改" in data["message"]

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_record_persistence():
    """测试打包记录跨进程重启后仍可查询"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_persistence"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, DELIVERY_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "PERSPKG")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--notes", "持久化测试包",
            "--json",
        ])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        package_id = data["package_id"]
        batch_id = data["batch_id"]

        result_list = run_photo_archive([
            "-c", str(config_path),
            "package", "list",
            "--json",
        ])
        assert result_list.returncode == 0
        list_data = json.loads(result_list.stdout)
        assert list_data["count"] >= 1
        found = any(p["package_id"] == package_id for p in list_data["packages"])
        assert found, "应该能找到刚才创建的打包记录"

        result_list_batch = run_photo_archive([
            "-c", str(config_path),
            "package", "list",
            "--batch-id", batch_id,
            "--json",
        ])
        assert result_list_batch.returncode == 0
        list_batch_data = json.loads(result_list_batch.stdout)
        assert list_batch_data["count"] >= 1
        assert all(p["batch_id"] == batch_id for p in list_batch_data["packages"])

        result_list_status = run_photo_archive([
            "-c", str(config_path),
            "package", "list",
            "--status", "completed",
            "--json",
        ])
        assert result_list_status.returncode == 0
        list_status_data = json.loads(result_list_status.stdout)
        assert list_status_data["count"] >= 1
        assert all(p["status"] == "completed" for p in list_status_data["packages"])

        result_show = run_photo_archive([
            "-c", str(config_path),
            "package", "show", package_id,
            "--json",
        ])
        assert result_show.returncode == 0
        show_data = json.loads(result_show.stdout)
        assert show_data["package_id"] == package_id
        assert show_data["batch_id"] == batch_id
        assert show_data["notes"] == "持久化测试包"
        assert show_data["status"] == "completed"

        result_show_files = run_photo_archive([
            "-c", str(config_path),
            "package", "show", package_id,
            "--files",
        ])
        assert result_show_files.returncode == 0
        assert "[OK] 打包详情" in result_show_files.stdout
        assert "打包ID:" in result_show_files.stdout
        assert "打包文件" in result_show_files.stdout

        result_show_not_found = run_photo_archive([
            "-c", str(config_path),
            "package", "show", "nonexistent-id",
            "--json",
        ])
        assert result_show_not_found.returncode == 1
        error_data = json.loads(result_show_not_found.stdout)
        assert "error" in error_data

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_json_text_consistency():
    """测试 JSON 和文本输出的一致性"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_consistency"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, DELIVERY_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "CONSPKG")

        result_json = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--notes", "一致性测试",
            "--dry-run",
            "--json",
        ])
        assert result_json.returncode == 0
        data = json.loads(result_json.stdout)
        dry_run_package_id = data["package_id"]

        result_text = run_photo_archive([
            "-c", str(config_path),
            "package", "show", dry_run_package_id,
        ])
        assert result_text.returncode == 0

        assert str(data["total_files"]) in result_text.stdout
        assert str(data["package_id"]) in result_text.stdout
        assert data["batch_name"] in result_text.stdout

        if DELIVERY_DIR.exists():
            shutil.rmtree(DELIVERY_DIR)

        result_create_json = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--notes", "一致性测试",
            "--json",
        ])
        assert result_create_json.returncode == 0
        create_data = json.loads(result_create_json.stdout)
        package_id = create_data["package_id"]

        result_list_json = run_photo_archive([
            "-c", str(config_path),
            "package", "list",
            "--json",
        ])
        assert result_list_json.returncode == 0
        list_data = json.loads(result_list_json.stdout)

        result_list_text = run_photo_archive([
            "-c", str(config_path),
            "package", "list",
        ])
        assert result_list_text.returncode == 0

        assert str(list_data["count"]) in result_list_text.stdout
        assert package_id in result_list_text.stdout

        result_show_json = run_photo_archive([
            "-c", str(config_path),
            "package", "show", package_id,
            "--json",
        ])
        assert result_show_json.returncode == 0
        show_data = json.loads(result_show_json.stdout)

        result_show_text = run_photo_archive([
            "-c", str(config_path),
            "package", "show", package_id,
        ])
        assert result_show_text.returncode == 0

        assert show_data["package_id"] in result_show_text.stdout
        assert show_data["batch_name"] in result_show_text.stdout
        assert str(show_data["total_files"]) in result_show_text.stdout
        assert str(show_data["copied_files"]) in result_show_text.stdout

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_camera_filter():
    """测试按机位筛选打包"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_camera"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"
    DELIVERY_DIR = SAMPLE_DIR / "delivery"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR, DELIVERY_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        config_path = SAMPLE_DIR / "config.yaml"
        source_dir = SAMPLE_DIR / "source_cards"
        manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

        setup_package_test_environment(SAMPLE_DIR, config_path, source_dir, manifest_path, "CAMPKG")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "create", str(DELIVERY_DIR),
            "--camera", "A",
            "--dry-run",
            "--json",
        ])
        assert result.returncode == 0
        data = json.loads(result.stdout)

        for item in data["items"]:
            assert item["camera"] == "A" or item["file_name"].startswith("A_")
        assert data["total_files"] == 3

    finally:
        for d in [SAMPLE_DIR, DELIVERY_DIR]:
            if d.exists():
                shutil.rmtree(d)


def test_package_list_no_records():
    """测试没有打包记录时的列表输出"""
    SAMPLE_DIR = Path(__file__).parent.parent / "test_package_empty"
    WORK_DIR = SAMPLE_DIR / "work"
    ARCHIVE_DIR = SAMPLE_DIR / "archive"

    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR]:
        if d.exists():
            shutil.rmtree(d)

    try:
        SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
        config_path = SAMPLE_DIR / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("cameras:\n  - A\n")
            f.write("hash_strategy: sha256\n")
            f.write(f"archive_dir: {ARCHIVE_DIR}\n")
            f.write(f"work_dir: {WORK_DIR}\n")

        result = run_photo_archive([
            "-c", str(config_path),
            "package", "list",
        ])
        assert result.returncode == 0
        assert "(没有打包记录)" in result.stdout

        result_json = run_photo_archive([
            "-c", str(config_path),
            "package", "list",
            "--json",
        ])
        assert result_json.returncode == 0
        data = json.loads(result_json.stdout)
        assert data["count"] == 0
        assert data["packages"] == []

    finally:
        for d in [SAMPLE_DIR]:
            if d.exists():
                shutil.rmtree(d)
