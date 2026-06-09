from pathlib import Path
import subprocess
import sys
import shutil
import os
import json

import pytest


TEST_DIR = Path(__file__).parent.parent.resolve()
SAMPLE_DIR = TEST_DIR / "test_sample_data"
WORK_DIR = TEST_DIR / "test_work"
ARCHIVE_DIR = TEST_DIR / "test_archive"


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


def test_init_sample():
    result = run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "3",
        "--photos", "5",
        "--batch", "WEDDING001",
    ])
    assert result.returncode == 0, f"init-sample failed: {result.stderr}"
    assert SAMPLE_DIR.exists()
    assert (SAMPLE_DIR / "source_cards").exists()
    assert (SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv").exists()
    assert (SAMPLE_DIR / "config.yaml").exists()


def test_full_workflow():
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "3",
        "--batch", "TEST001",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards"
    manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "test-batch",
    ])
    assert result.returncode == 0, f"scan failed: {result.stderr}"
    assert "发现文件: 7" in result.stdout or "scanned_count" in result.stdout

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest_path),
    ])
    assert result.returncode == 0, f"import-list failed: {result.stderr}"

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--json",
    ])
    assert result.returncode in [0, 4, 5], f"verify failed: {result.stderr}"
    verify_data = json.loads(result.stdout)
    assert verify_data["missing_count"] >= 1
    assert verify_data["duplicate_count"] >= 1

    result = run_photo_archive([
        "-c", str(config_path),
        "plan",
        "--json",
    ])
    assert result.returncode == 0, f"plan failed: {result.stderr}"
    plan_data = json.loads(result.stdout)
    assert plan_data["correction_count"] > 0

    corrections = plan_data["corrections"]
    copy_corrections = [c for c in corrections if c["type"] == "copy"]
    assert len(copy_corrections) > 0
    first_correction_id = copy_corrections[0]["id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--correction-id", first_correction_id,
        "--json",
    ])
    assert result.returncode == 0, f"apply failed: {result.stderr}"
    apply_data = json.loads(result.stdout)
    assert apply_data["applied_count"] == 1

    target_path = Path(copy_corrections[0]["target"])
    assert target_path.exists(), "修正后的目标文件应该存在"

    report_path = SAMPLE_DIR / "report_before_undo.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(report_path),
        "--format", "json",
    ])
    assert result.returncode in [0, 3, 4, 5], f"report failed: {result.stderr}"

    with open(report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)
    assert report_data["summary"]["active_corrections_count"] == 1
    assert len(report_data["missing_files"]) >= 1
    assert len(report_data["duplicate_files"]) >= 1

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
        "--json",
    ])
    assert result.returncode == 0, f"undo failed: {result.stderr}"
    undo_data = json.loads(result.stdout)
    assert undo_data["undone_count"] == 1

    assert not target_path.exists(), "撤销后目标文件应该被删除"

    report_path2 = SAMPLE_DIR / "report_after_undo.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(report_path2),
        "--format", "json",
    ])
    assert result.returncode in [0, 3, 4, 5], f"report after undo failed: {result.stderr}"

    with open(report_path2, "r", encoding="utf-8") as f:
        report_data2 = json.load(f)
    assert report_data2["summary"]["active_corrections_count"] == 0
    assert report_data2["summary"]["undone_corrections_count"] == 1

    assert len(report_data["missing_files"]) == len(report_data2["missing_files"])
    assert len(report_data["duplicate_files"]) == len(report_data2["duplicate_files"])


def test_corrupted_manifest_exit_code():
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    manifest_path = SAMPLE_DIR / "delivery_list" / "corrupted.csv"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("this is not a valid csv\nwith broken, structure\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest_path),
    ])
    assert result.returncode == 2, f"corrupted manifest should exit with code 2, got {result.returncode}"


def test_duplicate_filenames_exit_code():
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    manifest_path = SAMPLE_DIR / "delivery_list" / "duplicate.csv"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_TEST_0001.jpg,A,1\n")
        f.write("A_TEST_0001.jpg,A,1\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest_path),
    ])
    assert result.returncode == 2, f"duplicate filenames should exit with code 2, got {result.returncode}"


def test_no_undo_exit_code():
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "undo",
    ])
    assert result.returncode == 6, f"no undoable should exit with code 6, got {result.returncode}"


def test_hash_mismatch_exit_code():
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "2",
        "--batch", "HASH001",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write("A_HASH001_0001.jpg,invalid_hash_here,A,1\n")
        f.write("A_HASH001_0002.jpg,,A,2\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest_path)])

    result = run_photo_archive([
        "-c", str(config_path),
        "verify",
    ])
    assert result.returncode == 3, f"hash mismatch should exit with code 3, got {result.returncode}"


def test_history_persistence():
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
        "--batch", "HIST001",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards"
    manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "history-test",
        "--json",
    ])
    assert result.returncode == 0
    scan_data = json.loads(result.stdout)
    batch_id = scan_data["batch_id"]

    run_photo_archive(["-c", str(config_path), "import-list", str(manifest_path)])
    run_photo_archive(["-c", str(config_path), "verify"])
    run_photo_archive(["-c", str(config_path), "plan"])

    result = run_photo_archive([
        "-c", str(config_path),
        "list-batches",
        "--json",
    ])
    assert result.returncode == 0
    batches = json.loads(result.stdout)
    assert len(batches) == 1
    assert batches[0]["batch_id"] == batch_id
    assert batches[0]["file_count"] == 5

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--json",
    ])
    assert result.returncode == 0
    scan_data2 = json.loads(result.stdout)
    assert scan_data2["batch_id"] == batch_id, "Should reuse existing batch"
    assert scan_data2["scanned_count"] == 5


def test_csv_report_export():
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
        "--batch", "CSV001",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards"
    manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"
    report_path = SAMPLE_DIR / "report.csv"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest_path)])
    run_photo_archive(["-c", str(config_path), "verify"])

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(report_path),
        "--format", "csv",
    ])
    assert result.returncode in [0, 4, 5]
    assert report_path.exists()

    with open(report_path, "r", encoding="utf-8-sig") as f:
        content = f.read()
        assert "=== 批次概览 ===" in content
        assert "=== 文件映射 ===" in content
        assert "=== 缺片 ===" in content
        assert "=== 重复文件 ===" in content


def test_naming_rule_field_matching():
    """回归测试: 源文件名与清单目标名不一致时，应通过命名规则字段匹配，不应误报缺片"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "3",
        "--batch", "NAMETEST",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    for i, f in enumerate(source_dir.glob("*.jpg")):
        new_name = f"A_RAW_{i+1:04d}.jpg"
        f.rename(source_dir / new_name)

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_NAMETEST_0001.jpg,A,1\n")
        f.write("A_NAMETEST_0002.jpg,A,2\n")
        f.write("A_NAMETEST_0003.jpg,A,3\n")

    scan_result = run_photo_archive(["-c", str(config_path), "scan", str(source_dir), "--json"])
    assert scan_result.returncode == 0
    scan_data = json.loads(scan_result.stdout)
    assert scan_data["scanned_count"] == 3

    import_result = run_photo_archive(["-c", str(config_path), "import-list", str(manifest_path), "--json"])
    assert import_result.returncode == 0

    verify_result = run_photo_archive([
        "-c", str(config_path),
        "verify",
        "--json",
    ])
    assert verify_result.returncode == 0, f"verify failed: {verify_result.stderr}"
    verify_data = json.loads(verify_result.stdout)

    assert verify_data["missing_count"] == 0, f"Should not report missing: {verify_data['missing']}"
    assert verify_data["ok_count"] == 3, f"All should match OK, got ok_count={verify_data['ok_count']}"


def test_apply_hash_verification():
    """回归测试: apply 执行前必须重新校验源文件哈希，扫描后文件被篡改应阻止归档"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "2",
        "--batch", "APPLYHASH",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_APPLYHASH_0001.jpg,A,1\n")
        f.write("A_APPLYHASH_0002.jpg,A,2\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest_path)])

    verify_result = run_photo_archive(["-c", str(config_path), "verify", "--json"])
    assert verify_result.returncode == 0
    verify_data = json.loads(verify_result.stdout)
    assert verify_data["ok_count"] == 2

    run_photo_archive(["-c", str(config_path), "plan"])

    first_file = list(source_dir.glob("*.jpg"))[0]
    with open(first_file, "ab") as f:
        f.write(b"tampered content after scan")

    apply_result = run_photo_archive([
        "-c", str(config_path),
        "apply",
        "--json",
    ])
    assert apply_result.returncode == 3, f"Should exit with code 3 for hash mismatch, got {apply_result.returncode}"

    apply_data = json.loads(apply_result.stdout)
    assert apply_data["hash_mismatch_count"] >= 1
    assert len(apply_data["hash_mismatches"]) >= 1

    report_path = SAMPLE_DIR / "hash_report.json"
    report_result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(report_path),
        "--format", "json",
    ])
    assert report_result.returncode == 3

    with open(report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)

    assert report_data["summary"]["hash_mismatch_count"] >= 1
    assert len(report_data["hash_changes"]) >= 1

    archive_files = list((ARCHIVE_DIR / "history-test").glob("*")) if (ARCHIVE_DIR / "history-test").exists() else []
    archived_count = len([f for f in ARCHIVE_DIR.rglob("*.jpg")]) if ARCHIVE_DIR.exists() else 0
    assert archived_count == 1, f"Only 1 file should be archived (the untampered one), got {archived_count}"


def test_help_exit_codes():
    """测试退出码帮助命令"""
    result = run_photo_archive(["help-exit-codes"])
    assert result.returncode == 0
    assert "2 - 清单错误" in result.stdout
    assert "3 - 哈希不一致" in result.stdout
    assert "4 - 重复文件名" in result.stdout
    assert "5 - 缺片" in result.stdout
    assert "6 - 没有可撤销的操作" in result.stdout


def test_full_cli_workflow():
    """完整 CLI 流程测试: scan -> import-list -> verify -> plan -> apply -> report"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
        "--batch", "FULLFLOW",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    manifest_path = SAMPLE_DIR / "delivery_list" / "delivery_manifest.csv"
    report_path = SAMPLE_DIR / "full_report.json"

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {ARCHIVE_DIR}\n")
        f.write(f"work_dir: {WORK_DIR}\n")

    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_FULLFLOW_0001.jpg,A,1\n")
        f.write("A_FULLFLOW_0002.jpg,A,2\n")

    result = run_photo_archive(["-c", str(config_path), "scan", str(source_dir), "--batch-name", "fullflow-test"])
    assert result.returncode == 0

    result = run_photo_archive(["-c", str(config_path), "import-list", str(manifest_path)])
    assert result.returncode == 0

    result = run_photo_archive(["-c", str(config_path), "verify", "--json"])
    assert result.returncode == 0
    verify_data = json.loads(result.stdout)
    assert verify_data["ok_count"] == 2

    result = run_photo_archive(["-c", str(config_path), "plan", "--json"])
    assert result.returncode == 0
    plan_data = json.loads(result.stdout)
    assert plan_data["correction_count"] >= 2

    result = run_photo_archive(["-c", str(config_path), "apply", "--json"])
    assert result.returncode == 0
    apply_data = json.loads(result.stdout)
    assert apply_data["applied_count"] == 2

    archive_batch_dir = ARCHIVE_DIR / "fullflow-test"
    archived_files = list(archive_batch_dir.glob("*.jpg"))
    assert len(archived_files) == 2, f"Should have 2 archived files, got {len(archived_files)}"

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(report_path),
        "--format", "json",
    ])
    assert result.returncode == 0

    with open(report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)

    assert report_data["summary"]["ok_count"] == 2
    assert report_data["summary"]["missing_count"] == 0
    assert report_data["summary"]["duplicate_count"] == 0
    assert report_data["summary"]["hash_mismatch_count"] == 0
    assert report_data["summary"]["active_corrections_count"] == 2
    assert report_data["summary"]["total_delivery_items"] == 2

    batch_file = WORK_DIR / "batches"
    assert batch_file.exists()
    batch_json_files = list(batch_file.glob("*.json"))
    assert len(batch_json_files) >= 1

    for item in report_data["file_mappings"]:
        assert item["status"] == "ok"
        assert item["original_path"] != ""
        assert Path(item["original_path"]).exists() or Path(item["original_path"].replace("card_A", "fullflow-test")).exists()

