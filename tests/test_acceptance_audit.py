from pathlib import Path
import subprocess
import sys
import shutil
import os
import json
import csv
import tempfile
import stat

import pytest


TEST_DIR = Path(__file__).parent.parent.resolve()
WORK_DIR = TEST_DIR / "test_acceptance_work"
TEST_DATA_DIR = TEST_DIR / "test_acceptance_data"


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


def create_base_config():
    config_path = TEST_DATA_DIR / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n  - A\n  - B\n  - C\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"work_dir: {WORK_DIR}\n")
        f.write(f"archive_dir: {TEST_DATA_DIR / 'archive'}\n")
    return config_path


def create_acceptance_config(source_dir, **kwargs):
    config = {
        "client_name": kwargs.get("client_name", "测试客户"),
        "batch_name": kwargs.get("batch_name", "TEST-BATCH-001"),
        "source_dir": str(source_dir),
        "required_directories": kwargs.get("required_directories", ["photos", "videos"]),
        "required_files": kwargs.get("required_files", ["manifest.json", "checksums.sha256"]),
        "expected_photo_count": kwargs.get("expected_photo_count", 6),
        "allowed_extensions": kwargs.get("allowed_extensions", [".jpg", ".jpeg", ".png", ".mp4", ".json", ".sha256", ".csv", ".txt"]),
        "min_file_size": kwargs.get("min_file_size", 100),
        "max_file_size": kwargs.get("max_file_size", 10 * 1024 * 1024),
        "manifest_file": kwargs.get("manifest_file", "delivery_manifest.csv"),
        "rules": kwargs.get("rules", []),
    }
    config_path = TEST_DATA_DIR / "acceptance_config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        import yaml
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    return config_path


def create_test_data(source_dir):
    source_dir = Path(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    (source_dir / "photos").mkdir()
    (source_dir / "videos").mkdir()

    test_content = b"test image content " * 100

    for i in range(1, 7):
        photo_path = source_dir / "photos" / f"IMG_{i:04d}.jpg"
        with open(photo_path, "wb") as f:
            f.write(test_content + str(i).encode())

    video_path = source_dir / "videos" / "clip_0001.mp4"
    with open(video_path, "wb") as f:
        f.write(b"test video content " * 100)

    manifest_path = source_dir / "delivery_manifest.csv"
    with open(manifest_path, "w", encoding="utf-8-sig") as f:
        f.write("target_name\n")
        for i in range(1, 7):
            f.write(f"IMG_{i:04d}.jpg\n")
        f.write("clip_0001.mp4\n")

    with open(source_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump({"version": "1.0", "total_files": 7}, f)

    with open(source_dir / "checksums.sha256", "w", encoding="utf-8") as f:
        f.write("test_checksums\n")

    return source_dir


@pytest.fixture(autouse=True)
def setup_and_teardown():
    for d in [WORK_DIR, TEST_DATA_DIR]:
        if d.exists():
            shutil.rmtree(d)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield
    for d in [WORK_DIR, TEST_DATA_DIR]:
        if d.exists():
            shutil.rmtree(d)


def test_successful_acceptance_audit():
    """测试成功的验收审计"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    json_export = TEST_DATA_DIR / "report.json"
    csv_export = TEST_DATA_DIR / "report.csv"

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
        "--json", str(json_export),
        "--csv", str(csv_export),
    ])

    assert result.returncode == 0, f"acceptance run failed: {result.stderr}\nstdout: {result.stdout}"
    assert "交付验收审计报告" in result.stdout
    assert "总体结果:  ✓ 通过" in result.stdout

    assert json_export.exists(), "JSON report not created"
    assert csv_export.exists(), "CSV report not created"

    with open(json_export, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    assert json_data["status"] == "pass"
    stats = json_data["statistics"]
    assert stats["overall"] == "PASS"
    assert stats["total_rules"] > 0
    assert stats["failed"] == 0

    result_list = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "list",
        "--json",
    ])
    assert result_list.returncode == 0
    list_data = json.loads(result_list.stdout)
    assert list_data["count"] >= 1

    audit_id = list_data["audits"][0]["audit_id"]
    result_show = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "show", audit_id,
        "--json",
    ])
    assert result_show.returncode == 0
    show_data = json.loads(result_show.stdout)
    assert show_data["audit_id"] == audit_id
    assert show_data["status"] == "pass"


def test_config_missing_fields():
    """测试配置缺少必填字段"""
    base_config = create_base_config()
    acceptance_config = TEST_DATA_DIR / "bad_config.yaml"
    with open(acceptance_config, "w", encoding="utf-8") as f:
        f.write("client_name: 测试客户\n")
        f.write("batch_name: TEST-001\n")

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])

    assert result.returncode == 15, f"Expected exit code 15, got {result.returncode}"
    assert "验收配置错误" in result.stderr or "acceptance_config_error" in result.stdout
    assert "缺少字段" in result.stderr or "missing_fields" in result.stdout


def test_directory_not_found():
    """测试待检查目录不存在"""
    base_config = create_base_config()
    nonexistent_dir = TEST_DATA_DIR / "nonexistent"
    acceptance_config = create_acceptance_config(nonexistent_dir)

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])

    assert result.returncode == 17, f"Expected exit code 17, got {result.returncode}"
    assert "目录不存在" in result.stderr or "acceptance_directory_not_found" in result.stdout


def test_export_conflict_without_overwrite():
    """测试导出文件已存在，不使用 --overwrite"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    json_export = TEST_DATA_DIR / "report.json"
    with open(json_export, "w", encoding="utf-8") as f:
        f.write("existing content")

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
        "--json", str(json_export),
    ])

    assert result.returncode == 18, f"Expected exit code 18, got {result.returncode}"
    assert "导出文件已存在" in result.stderr or "acceptance_export_exists" in result.stdout

    with open(json_export, "r", encoding="utf-8") as f:
        assert f.read() == "existing content", "File should not be overwritten"


def test_export_with_overwrite():
    """测试导出文件已存在，使用 --overwrite"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    json_export = TEST_DATA_DIR / "report.json"
    with open(json_export, "w", encoding="utf-8") as f:
        f.write("existing content")

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
        "--json", str(json_export),
        "--overwrite",
    ])

    assert result.returncode == 0, f"Expected exit code 0, got {result.returncode}"

    with open(json_export, "r", encoding="utf-8") as f:
        content = f.read()
        assert content != "existing content", "File should be overwritten"
        data = json.loads(content)
        assert "audit_id" in data


def test_readonly_directory_write_error():
    """测试只读目录写入失败"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    readonly_dir = TEST_DATA_DIR / "readonly_export"
    readonly_dir.mkdir()

    import sys
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["icacls", str(readonly_dir), "/deny", f"{os.environ.get('USERNAME', 'Everyone')}:(OI)(CI)(W)"],
                check=True,
                capture_output=True,
            )
        else:
            os.chmod(readonly_dir, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

        json_export = readonly_dir / "report.json"
        result = run_photo_archive([
            "-c", str(base_config),
            "acceptance", "run", str(acceptance_config),
            "--json", str(json_export),
        ])

        assert result.returncode == 19, f"Expected exit code 19, got {result.returncode}. stderr: {result.stderr}"
        assert "写入权限错误" in result.stderr or "acceptance_write_permission_error" in result.stdout
    finally:
        if sys.platform == "win32":
            subprocess.run(
                ["icacls", str(readonly_dir), "/reset"],
                check=True,
                capture_output=True,
            )
        else:
            os.chmod(readonly_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)


def test_audit_with_failures():
    """测试存在检查失败的验收审计"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")

    (source_dir / "manifest.json").unlink()

    acceptance_config = create_acceptance_config(
        source_dir,
        expected_photo_count=100,
        required_files=["manifest.json", "checksums.sha256"],
    )

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])

    assert result.returncode == 20, f"Expected exit code 20, got {result.returncode}"
    assert "总体结果:  ✗ 失败" in result.stdout
    assert "缺少" in result.stdout or "不足" in result.stdout


def test_rule_conflict():
    """测试规则冲突（同一类型多个启用规则）"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")

    conflicting_rules = [
        {
            "rule_id": "photo_count_1",
            "rule_type": "photo_count",
            "description": "第一个数量规则",
            "enabled": True,
            "parameters": {"expected_count": 10},
        },
        {
            "rule_id": "photo_count_2",
            "rule_type": "photo_count",
            "description": "第二个数量规则",
            "enabled": True,
            "parameters": {"expected_count": 20},
        },
    ]

    acceptance_config = create_acceptance_config(
        source_dir,
        rules=conflicting_rules,
    )

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])

    assert result.returncode == 16, f"Expected exit code 16, got {result.returncode}"
    assert "规则冲突" in result.stderr or "acceptance_rule_conflict" in result.stdout


def test_history_query_after_restart_simulation():
    """测试跨进程重启后查询历史记录"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    result1 = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])
    assert result1.returncode == 0

    list_result1 = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "list",
        "--json",
    ])
    list_data1 = json.loads(list_result1.stdout)
    first_audit_id = list_data1["audits"][0]["audit_id"]

    source_dir2 = create_test_data(TEST_DATA_DIR / "delivery2")
    acceptance_config2 = create_acceptance_config(
        source_dir2,
        batch_name="TEST-BATCH-002",
    )
    result2 = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config2),
    ])
    assert result2.returncode == 0

    list_result2 = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "list",
        "--json",
    ])
    list_data2 = json.loads(list_result2.stdout)
    assert list_data2["count"] >= 2

    batch_filter_result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "list",
        "--batch", "TEST-BATCH-002",
        "--json",
    ])
    batch_data = json.loads(batch_filter_result.stdout)
    assert batch_data["count"] >= 1
    for audit in batch_data["audits"]:
        assert audit["batch_name"] == "TEST-BATCH-002"

    client_filter_result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "list",
        "--client", "测试客户",
        "--json",
    ])
    client_data = json.loads(client_filter_result.stdout)
    assert client_data["count"] >= 2
    for audit in client_data["audits"]:
        assert audit["client_name"] == "测试客户"

    show_result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "show", first_audit_id,
        "--json",
    ])
    show_data = json.loads(show_result.stdout)
    assert show_data["audit_id"] == first_audit_id
    assert "results" in show_data
    assert "statistics" in show_data


def test_reexport_audit_result():
    """测试重新导出历史审计结果"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])

    list_result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "list",
        "--json",
    ])
    list_data = json.loads(list_result.stdout)
    audit_id = list_data["audits"][0]["audit_id"]

    new_json_export = TEST_DATA_DIR / "reexport_report.json"
    new_csv_export = TEST_DATA_DIR / "reexport_report.csv"

    reexport_result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "reexport", audit_id,
        "--json", str(new_json_export),
        "--csv", str(new_csv_export),
    ])

    assert reexport_result.returncode == 0, f"reexport failed: {reexport_result.stderr}"
    assert new_json_export.exists()
    assert new_csv_export.exists()

    with open(new_json_export, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    assert json_data["audit_id"] == audit_id
    assert "results" in json_data
    assert "statistics" in json_data


def test_json_csv_content_consistency():
    """测试 JSON 和 CSV 内容一致性"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    json_export = TEST_DATA_DIR / "consistency_report.json"
    csv_export = TEST_DATA_DIR / "consistency_report.csv"

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
        "--json", str(json_export),
        "--csv", str(csv_export),
    ])

    assert result.returncode == 0

    with open(json_export, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    csv_content = []
    with open(csv_export, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            csv_content.append(row)

    csv_text = "\n".join([",".join(row) for row in csv_content])

    assert json_data["audit_id"] in csv_text
    assert json_data["client_name"] in csv_text
    assert json_data["batch_name"] in csv_text
    assert str(json_data["statistics"]["total_rules"]) in csv_text
    assert str(json_data["statistics"]["passed"]) in csv_text
    assert str(json_data["statistics"]["failed"]) in csv_text

    for r in json_data["results"]:
        assert r["rule_id"] in csv_text
        assert r["message"] in csv_text


def test_duplicate_filename_detection():
    """测试重复文件名检测"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")

    photo_content = b"duplicate test content"
    with open(source_dir / "photos" / "IMG_0001.jpg", "rb") as f:
        photo_content = f.read()

    dup_dir = source_dir / "photos" / "duplicate_folder"
    dup_dir.mkdir()
    with open(dup_dir / "IMG_0001.jpg", "wb") as f:
        f.write(photo_content)

    acceptance_config = create_acceptance_config(source_dir)

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])

    assert result.returncode == 20, f"Expected exit code 20, got {result.returncode}"
    assert "重复文件名" in result.stdout or "duplicate" in result.stdout.lower()


def test_text_summary_output():
    """测试文本摘要输出"""
    base_config = create_base_config()
    source_dir = create_test_data(TEST_DATA_DIR / "delivery")
    acceptance_config = create_acceptance_config(source_dir)

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
        "--no-summary",
    ])

    assert result.returncode == 0
    assert "交付验收审计报告" not in result.stdout

    result2 = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(acceptance_config),
    ])

    assert "交付验收审计报告" in result2.stdout
    assert "=" * 60 in result2.stdout
    assert "统计摘要" in result2.stdout
    assert "检查结果明细" in result2.stdout


def test_acceptance_config_not_found():
    """测试验收配置文件不存在"""
    base_config = create_base_config()
    nonexistent_config = TEST_DATA_DIR / "nonexistent_config.yaml"

    result = run_photo_archive([
        "-c", str(base_config),
        "acceptance", "run", str(nonexistent_config),
    ])

    assert result.returncode == 15, f"Expected exit code 15, got {result.returncode}"
    assert "验收配置文件不存在" in result.stderr or "acceptance_config_error" in result.stdout
