from pathlib import Path
import subprocess
import sys
import shutil
import os
import json
import time

import pytest


TEST_DIR = Path(__file__).parent.parent.resolve()
SAMPLE_DIR = TEST_DIR / "test_queue_data"
WORK_DIR = TEST_DIR / "test_queue_work"
ARCHIVE_DIR = TEST_DIR / "test_queue_archive"


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
    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR]:
        if d.exists():
            shutil.rmtree(d)
    yield
    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR]:
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


def test_save_snapshot_and_resume_after_restart():
    """测试保存批次快照后，重启进程可以继续执行剩余项"""
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
        f.write(f"A_RESUME01_0001.jpg,{hash1},A,1\n")
        f.write(f"A_RESUME01_0002.jpg,{hash2},A,2\n")
        f.write(f"A_RESUME01_0003.jpg,{hash3},A,3\n")
        f.write(f"A_RESUME01_0004.jpg,{hash4},A,4\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "resume-test",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest),
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--save", "batch-2024-job-001",
        "--description", "2024年客户第一批归档任务",
        "--json",
    ])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)
    assert "snapshot_id" in plan_data
    assert plan_data["snapshot_name"] == "batch-2024-job-001"
    snapshot_id = plan_data["snapshot_id"]
    assert plan_data["pending_count"] == 4
    assert "statistics" in plan_data
    assert plan_data["statistics"]["pending"] == 4

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--from-snapshot", snapshot_id,
        "--limit", "2",
        "--json",
    ])
    assert result.returncode == 0
    apply1 = json.loads(result.stdout)
    assert apply1["applied_count"] == 2
    assert apply1["from_snapshot"] == snapshot_id
    assert apply1["remaining_count"] == 2
    assert "statistics" in apply1
    assert apply1["statistics"]["completed"] == 2
    assert apply1["statistics"]["pending"] == 2

    archive_dir = ARCHIVE_DIR / "resume-test"
    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status1 = json.loads(result.stdout)
    assert status1["completed_count"] == 2
    assert status1["pending_count"] == 2
    assert status1["progress_percent"] == 50
    assert len(status1["snapshots"]) == 1
    assert status1["snapshots"][0]["snapshot_id"] == snapshot_id
    assert status1["snapshots"][0]["is_active"] == True
    assert status1["snapshots"][0]["snapshot_progress"] == 50

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--from-snapshot", snapshot_id,
        "--json",
    ])
    assert result.returncode == 0
    apply2 = json.loads(result.stdout)
    assert apply2["applied_count"] == 2
    assert apply2["skipped_count"] == 2
    assert apply2["remaining_count"] == 0
    assert apply2["statistics"]["completed"] == 4
    assert apply2["statistics"]["pending"] == 0

    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 4

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status2 = json.loads(result.stdout)
    assert status2["completed_count"] == 4
    assert status2["pending_count"] == 0
    assert status2["progress_percent"] == 100
    assert status2["snapshots"][0]["snapshot_progress"] == 100


def test_conflict_detection_no_overwrite():
    """测试目标文件被外部修改时，检测到冲突并不静默覆盖"""
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
        f.write(f"A_CONFLICT02_0001.jpg,{hash1},A,1\n")
        f.write(f"A_CONFLICT02_0002.jpg,{hash2},A,2\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "conflict-test-2",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest),
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--save", "conflict-test-batch",
        "--json",
    ])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)
    snapshot_id = plan_data["snapshot_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--from-snapshot", snapshot_id,
        "--json",
    ])
    assert result.returncode == 0
    apply1 = json.loads(result.stdout)
    assert apply1["applied_count"] == 2
    assert apply1["conflict_count"] == 0

    archive_dir = ARCHIVE_DIR / "conflict-test-2"
    target_file = archive_dir / "A_CONFLICT02_0001.jpg"
    assert target_file.exists()
    original_target_hash = get_file_hash(target_file)

    with open(target_file, "wb") as f:
        f.write(b"externally modified content")

    modified_hash = get_file_hash(target_file)
    assert modified_hash != original_target_hash

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--from-snapshot", snapshot_id,
        "--no-resume",
        "--json",
    ])
    assert result.returncode == 9
    apply2 = json.loads(result.stdout)
    assert apply2["conflict_count"] >= 1
    assert len(apply2["conflicts"]) >= 1

    conflict = apply2["conflicts"][0]
    assert conflict["conflict_type"] == "external_modification"
    assert "外部修改" in conflict["message"]
    assert conflict["details"]["expected_hash"] == original_target_hash
    assert conflict["details"]["actual_hash"] == modified_hash

    assert apply2["applied_count"] == 1
    assert apply2["skipped_count"] == 0
    assert apply2["statistics"]["conflicted"] >= 1
    assert apply2["statistics"]["completed"] == 1
    assert apply2["statistics"]["pending"] == 0

    with open(target_file, "rb") as f:
        content_after = f.read()
    assert content_after == b"externally modified content", "冲突时不应覆盖外部修改的文件"

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status_data = json.loads(result.stdout)
    assert status_data["conflicted_count"] >= 1
    assert "unresolved_conflicts" in status_data
    assert len(status_data["unresolved_conflicts"]) >= 1


def test_json_output_fields_stable():
    """测试 JSON 输出字段在多次调用间保持稳定，包含完整的批次进度信息"""
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
        f.write(f"A_STABLE01_0001.jpg,{hash1},A,1\n")
        f.write(f"A_STABLE01_0002.jpg,{hash2},A,2\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "stable-json-test",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest),
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--save", "stable-batch",
        "--json",
    ])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)

    expected_plan_fields = {
        "batch_id", "batch_name", "archive_dir", "correction_count",
        "pending_count", "applied_count", "undone_count", "conflicted_count",
        "failed_count", "skipped_count", "undoable_count",
        "corrections", "pending_corrections", "statistics",
        "snapshot_id", "snapshot_name", "snapshot_correction_count",
    }
    actual_plan_fields = set(plan_data.keys())
    assert expected_plan_fields.issubset(actual_plan_fields), f"plan JSON 缺少字段: {expected_plan_fields - actual_plan_fields}"

    stats_fields = {"total", "pending", "completed", "conflicted", "failed", "skipped", "rolled_back", "undoable"}
    assert set(plan_data["statistics"].keys()) == stats_fields

    corr_fields = {"id", "type", "source", "target", "reason", "status", "applied",
                   "applied_at", "rolled_back", "source_hash", "expected_target_hash",
                   "actual_target_hash", "failure_reason", "conflict_details",
                   "completed_at", "plan_snapshot_id"}
    assert set(plan_data["corrections"][0].keys()) == corr_fields

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    apply_data = json.loads(result.stdout)

    expected_apply_fields = {
        "batch_id", "batch_name", "apply_id", "applied_count", "skipped_count",
        "failed_count", "conflict_count", "remaining_count", "total_count",
        "hash_mismatch_count", "from_snapshot", "applied", "skipped", "failed",
        "conflicts", "hash_mismatches", "applied_ids", "skipped_ids",
        "failed_ids", "conflicted_ids", "limit", "target_correction_id",
        "statistics",
    }
    actual_apply_fields = set(apply_data.keys())
    assert expected_apply_fields.issubset(actual_apply_fields), f"apply JSON 缺少字段: {expected_apply_fields - actual_apply_fields}"

    assert set(apply_data["statistics"].keys()) == stats_fields

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status_data = json.loads(result.stdout)

    expected_status_fields = {
        "batch_id", "batch_name", "created_at", "updated_at", "total_count",
        "pending_count", "completed_count", "conflicted_count", "failed_count",
        "skipped_count", "rolled_back_count", "undoable_count", "progress_percent",
        "statistics", "pending", "completed", "conflicted", "failed",
        "skipped", "rolled_back", "snapshots", "active_snapshot_id",
        "last_plan_at", "last_apply_at", "last_undo_at", "file_count",
        "delivery_count",
    }
    actual_status_fields = set(status_data.keys())
    assert expected_status_fields.issubset(actual_status_fields), f"status JSON 缺少字段: {expected_status_fields - actual_status_fields}"

    assert set(status_data["statistics"].keys()) == stats_fields

    snapshot_fields = {"snapshot_id", "name", "created_at", "correction_count",
                       "archive_dir", "description", "is_active",
                       "snapshot_total", "snapshot_pending", "snapshot_completed",
                       "snapshot_conflicted", "snapshot_failed", "snapshot_skipped",
                       "snapshot_rolled_back", "snapshot_progress"}
    assert set(status_data["snapshots"][0].keys()) == snapshot_fields

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    undo_data = json.loads(result.stdout)

    expected_undo_fields = {
        "batch_id", "batch_name", "undo_id", "undone_count", "failed_count",
        "total_applied_before", "remaining_applied_after", "undone", "failed",
        "undone_ids", "failed_ids", "target_correction_id", "statistics",
    }
    actual_undo_fields = set(undo_data.keys())
    assert expected_undo_fields.issubset(actual_undo_fields), f"undo JSON 缺少字段: {expected_undo_fields - actual_undo_fields}"

    assert set(undo_data["statistics"].keys()) == stats_fields


def test_undo_rollback_status_correctly():
    """测试撤销操作后，状态正确回退，统计数据准确更新"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    f1 = create_source_file(source_dir, "IMG_0001.jpg", b"photo1content")
    f2 = create_source_file(source_dir, "IMG_0002.jpg", b"photo2content")
    f3 = create_source_file(source_dir, "IMG_0003.jpg", b"photo3content")
    hash1 = get_file_hash(f1)
    hash2 = get_file_hash(f2)
    hash3 = get_file_hash(f3)

    manifest_dir = SAMPLE_DIR / "delivery_list"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = manifest_dir / "m1.csv"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_UNDO01_0001.jpg,{hash1},A,1\n")
        f.write(f"A_UNDO01_0002.jpg,{hash2},A,2\n")
        f.write(f"A_UNDO01_0003.jpg,{hash3},A,3\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "undo-rollback-test",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest),
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)
    assert plan_data["statistics"]["pending"] == 3
    assert plan_data["statistics"]["completed"] == 0
    assert plan_data["statistics"]["rolled_back"] == 0
    assert plan_data["statistics"]["undoable"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--limit", "2",
        "--json",
    ])
    assert result.returncode == 0
    apply1 = json.loads(result.stdout)
    assert apply1["statistics"]["completed"] == 2
    assert apply1["statistics"]["pending"] == 1
    assert apply1["statistics"]["undoable"] == 2

    archive_dir = ARCHIVE_DIR / "undo-rollback-test"
    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status1 = json.loads(result.stdout)
    assert status1["completed_count"] == 2
    assert status1["pending_count"] == 1
    assert status1["undoable_count"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    apply2 = json.loads(result.stdout)
    assert apply2["statistics"]["completed"] == 3
    assert apply2["statistics"]["pending"] == 0
    assert apply2["statistics"]["undoable"] == 3

    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 3

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    undo1 = json.loads(result.stdout)
    assert undo1["undone_count"] == 3
    assert undo1["statistics"]["completed"] == 0
    assert undo1["statistics"]["rolled_back"] == 3
    assert undo1["statistics"]["undoable"] == 0
    assert undo1["statistics"]["pending"] == 0

    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 0, "撤销后应删除已归档的文件"

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status2 = json.loads(result.stdout)
    assert status2["completed_count"] == 0
    assert status2["rolled_back_count"] == 3
    assert status2["undoable_count"] == 0

    corrections = status2["rolled_back"]
    assert len(corrections) == 3
    for c in corrections:
        assert c["status"] == "rolled_back"
        assert c["rolled_back"] == True
        assert c["applied"] == False

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    apply3 = json.loads(result.stdout)
    assert apply3["applied_count"] == 0
    assert apply3["remaining_count"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    plan2 = json.loads(result.stdout)
    assert plan2["statistics"]["rolled_back"] == 3
    assert plan2["statistics"]["pending"] == 3
    assert plan2["statistics"]["completed"] == 0


def test_skip_conflicts_state_consistency():
    """测试 --skip-conflicts 后状态一致性：冲突解决后 apply/status/undo/plan 状态统一"""
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
        f.write(f"A_TESTBATCH_0001.jpg,{hash1},A,1\n")
        f.write(f"A_TESTBATCH_0002.jpg,{hash2},A,2\n")

    batch_name = "A_TESTBATCH"

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", batch_name,
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
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)
    assert plan_data["correction_count"] == 2
    assert plan_data["statistics"]["pending"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    apply1 = json.loads(result.stdout)
    assert apply1["applied_count"] == 2
    assert apply1["statistics"]["completed"] == 2

    batch_archive_dir = ARCHIVE_DIR / batch_name
    target_files = list(batch_archive_dir.glob("*.jpg"))
    assert len(target_files) == 2
    target_file_1 = batch_archive_dir / "A_TESTBATCH_0001.jpg"
    target_file_2 = batch_archive_dir / "A_TESTBATCH_0002.jpg"
    assert target_file_1.exists()
    assert target_file_2.exists()

    original_target_hash_1 = get_file_hash(target_file_1)
    with open(target_file_1, "wb") as f:
        f.write(b"externally modified content for conflict test")
    modified_hash_1 = get_file_hash(target_file_1)
    assert modified_hash_1 != original_target_hash_1

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--no-resume",
        "--json",
    ])
    assert result.returncode == 9
    apply2 = json.loads(result.stdout)
    assert apply2["conflict_count"] >= 1
    assert apply2["statistics"]["conflicted"] >= 1

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status1 = json.loads(result.stdout)
    assert status1["unresolved_conflicts_count"] >= 1
    assert status1["statistics"]["conflicted"] >= 1
    assert status1["statistics"]["completed"] == 1
    assert status1["statistics"]["pending"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 7
    assert "未解决的冲突" in result.stderr or "conflict" in result.stderr.lower()

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 7
    assert "未解决的冲突" in result.stderr or "conflict" in result.stderr.lower()

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--skip-conflicts",
        "--no-resume",
        "--json",
    ])
    assert result.returncode == 9
    apply3 = json.loads(result.stdout)
    assert apply3["applied_count"] == 1
    assert apply3["conflict_count"] >= 1
    assert apply3["statistics"]["conflicted"] >= 1
    assert apply3["statistics"]["completed"] == 1

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status2 = json.loads(result.stdout)
    assert status2["unresolved_conflicts_count"] >= 1
    assert status2["statistics"]["conflicted"] >= 1
    assert status2["statistics"]["completed"] == 1

    target_file_1.unlink()

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--batch-id", batch_id,
        "--skip-conflicts",
        "--no-resume",
        "--json",
    ])
    assert result.returncode == 0
    apply4 = json.loads(result.stdout)
    assert apply4["applied_count"] == 2
    assert apply4["conflict_count"] == 0
    assert apply4["statistics"]["completed"] == 2
    assert apply4["statistics"]["conflicted"] == 0
    assert apply4["statistics"]["pending"] == 0

    with open(target_file_1, "rb") as f:
        content_after = f.read()
    assert content_after == b"photo1content", "冲突解决后应正确应用"

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status3 = json.loads(result.stdout)
    assert status3.get("unresolved_conflicts_count", 0) == 0
    assert "unresolved_conflicts" not in status3 or len(status3.get("unresolved_conflicts", [])) == 0
    assert status3["statistics"]["completed"] == 2
    assert status3["statistics"]["conflicted"] == 0
    assert status3["statistics"]["undoable"] == 2
    assert status3["statistics"]["pending"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
    ])
    assert result.returncode == 0
    assert "未解决的冲突" not in result.stdout
    assert "待执行: 0" in result.stdout
    assert "已完成: 2" in result.stdout
    assert "冲突: 0" in result.stdout
    assert "可撤销: 2" in result.stdout
    assert "执行进度: 100% (2/2)" in result.stdout

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    undo_data = json.loads(result.stdout)
    assert undo_data["undone_count"] == 2
    assert undo_data["statistics"]["rolled_back"] == 2
    assert undo_data["statistics"]["completed"] == 0
    assert undo_data["statistics"]["undoable"] == 0

    target_files = list(batch_archive_dir.glob("*.jpg"))
    assert len(target_files) == 0, "撤销后归档目录应为空"

    result = run_photo_archive([
        "-c", str(config_path),
        "status",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    status4 = json.loads(result.stdout)
    assert status4["statistics"]["rolled_back"] == 2
    assert status4["statistics"]["completed"] == 0
    assert status4.get("unresolved_conflicts_count", 0) == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    plan2 = json.loads(result.stdout)
    assert plan2["statistics"]["pending"] == 2
    assert plan2["statistics"]["rolled_back"] == 2
    assert plan2["statistics"]["completed"] == 0

    status_corrections = status3["completed"]
    status_ids = set(c["id"] for c in status_corrections)
    apply_corrections = apply4["applied"]
    apply_ids = set(c["id"] for c in apply_corrections)
    assert status_ids == apply_ids, "status 和 apply 应引用相同的修正ID"

    for c in status_corrections:
        assert c["status"] == "completed"
        assert c["applied"] is True
        assert c["rolled_back"] is False
        assert c["completed_at"] is not None
        assert c["failure_reason"] is None

    for c in apply_corrections:
        assert c["status"] == "completed"
        assert c["applied"] is True
