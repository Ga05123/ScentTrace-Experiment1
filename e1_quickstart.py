#!/usr/bin/env python3
"""Standalone installer/probe/runner for the frozen ScentTrace E1 code."""
import argparse, getpass, hashlib, json, os, shutil, subprocess, sys, venv, zipfile
from pathlib import Path
from datetime import datetime, timezone
from e1_progress import run_command

ROOT = Path(__file__).resolve().parent
CODE = ROOT / "framework"
WORK = ROOT / "_e1_work"
FOLDER_ID = "1FPJTMRfneLGu63whG1MxP60Hca3S-UwG"
REQUIRED = ("run_batch.py", "experiment1.py", "resolve_sources.py",
            "GOAL_CODEBOOK.md", "ALIGNMENT_CODEBOOK.md", "OPERATOR_CODEBOOK.md",
            "config_api_template.json", "requirements.txt", "schemas.json")

def stop(msg):
    raise SystemExit("STOP: " + msg)

def execute(args, cwd=None, env=None):
    print("RUN", args[0], *(str(x) for x in args[1:]), flush=True)
    r = subprocess.run([str(x) for x in args], cwd=cwd, env=env)
    if r.returncode:
        stop(f"process exited {r.returncode}; no fallback was made")

def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def code_location(args):
    path = Path(args.code_dir).resolve() if args.code_dir else CODE
    missing = [f for f in REQUIRED if not (path / f).is_file()]
    if missing:
        stop("missing R3 files: " + ", ".join(missing))
    return path

def vpy():
    return WORK / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

def source_jsonl():
    return WORK / "runs" / "inputs" / "qualified_120_absolute.jsonl"

def setup(args):
    code = code_location(args)
    data = WORK / "data"
    data.mkdir(parents=True, exist_ok=True)
    if args.local_data:
        origin = Path(args.local_data).resolve()
        if not (origin / "PACKAGE_INDEX.json").is_file():
            stop("local data package directory missing PACKAGE_INDEX.json")
        if origin != data:
            shutil.copytree(origin, data, dirs_exist_ok=True)
    else:
        # Google Drive folder is public. Use gdown; no Google login/rclone required.
        if not vpy().is_file():
            venv.EnvBuilder(with_pip=True).create(WORK / ".venv")
        check = subprocess.run([str(vpy()), "-c", "import gdown,sys; sys.exit(0 if gdown.__version__ == '6.4.2' else 1)"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if check.returncode:
            execute([vpy(), "-m", "pip", "install", "gdown==6.4.2"])
        import_url = "https://drive.google.com/drive/folders/" + FOLDER_ID
        script = ("import gdown,sys; "
                  "result=gdown.download_folder(url=sys.argv[1],output=sys.argv[2],"
                  "use_cookies=False,resume=True); "
                  "sys.exit(0 if result else 3)")
        execute([vpy(), "-c", script, import_url, str(data) + os.sep])
    verifier = data / "unpack_and_verify.py"
    if not verifier.is_file() or not (data / "PACKAGE_INDEX.json").is_file():
        stop("source verifier or index not downloaded")
    execute([sys.executable, verifier], cwd=data)
    if not vpy().is_file():
        venv.EnvBuilder(with_pip=True).create(WORK / ".venv")
    execute([vpy(), "-m", "pip", "install", "-r", code / "requirements.txt"], cwd=code)
    source_jsonl().parent.mkdir(parents=True, exist_ok=True)
    if not source_jsonl().is_file():
        execute([vpy(), code / "resolve_sources.py", "--source-package",
                 data / "SOURCE_PACKAGE_120", "--output", source_jsonl()], cwd=code)
    execute([vpy(), "-m", "unittest", "test_teacher_delivery", "-q"], cwd=ROOT)
    ensure_cohort_authorization()
    print("数据准备完成：120 条轨迹，可以开始试跑。")


def ensure_cohort_authorization():
    manifest = ROOT / "docs" / "COHORT_AUTHORIZATION_120.json"
    if not manifest.is_file():
        stop("120-cohort authorization manifest missing")
    a = json.loads(manifest.read_text(encoding="utf-8"))
    if (a.get("cohort") != "E1_PUBLIC_SOURCE_120" or a.get("authorized_for") != "MODEL_ANNOTATION_COHORT"
            or a.get("expected_trajectories") != 120 or a.get("expected_families") != 40):
        stop("cohort authorization manifest is invalid")
    # Authorization is for an annotated derivative; original SOURCE_ONLY roles are immutable.
    with source_jsonl().open(encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    by_dataset = {}
    family_ids = set()
    for obj in rows:
        if (obj.get("split"), obj.get("trial_role")) != (
                "SOURCE_ONLY_NOT_E1_SEMANTICALLY_REVIEWED", "SOURCE_INTEGRITY_AUDIT_ONLY"):
            stop("unexpected native source role; do not relabel the original source")
        by_dataset[obj["dataset"]] = by_dataset.get(obj["dataset"],0) + 1
        family_ids.add((obj["dataset"], obj["family_id"]))
    if (len(rows)!=120 or len(family_ids)!=40 or by_dataset!=a["expected_by_dataset"]):
        stop("cohort scope does not match approved 120 dataset/40 task families")
    if (a.get("source_manifest_sha256") and
            hash_file(WORK / "data" / "SOURCE_PACKAGE_120" / "FILE_MANIFEST.jsonl")
            != a["source_manifest_sha256"]):
        stop("official source manifest hash changed")
    return a

def check_batch_complete(batch):
    path=batch/"BATCH_EXECUTION_STATUS.json"
    if not path.is_file():
        stop("inference returned without final batch checkpoint: "+str(batch))
    result=json.loads(path.read_text(encoding="utf-8"))
    if result.get("status")!="SELECTED_MODEL_STAGES_COMPLETE" or result.get("failures"):
        stop("标注尚未全部完成，请检查：" + str(path))
    return result

def prepared_code(args):
    code = code_location(args)
    if not vpy().is_file() or not source_jsonl().is_file():
        stop("setup has not completed")
    return code

def config(code):
    p = WORK / "config_api.json"
    if not p.is_file():
        cfg = json.loads((code / "config_api_template.json").read_text(encoding="utf-8"))
        cfg["model"]["base_url"] = input("API 地址（包含 /v1）：").strip()
        cfg["model"]["model_id"] = input("模型 ID：").strip()
        cfg["model"]["api_key_env"] = "SCENTTRACE_API_KEY"
        cfg["model"]["inference_authorized"] = False
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("API 配置已保存：", p)
    m = json.loads(p.read_text(encoding="utf-8"))["model"]
    if not m.get("base_url", "").startswith("https://") or not m.get("model_id"):
        stop("configure real HTTPS API endpoint and model name")
    if m["capabilities"].get("images") is not True or m["capabilities"].get("structured_output") != "json_schema":
        stop("required multimodal image / full JSON Schema mode disabled")
    return p

def key_env():
    # Explicit probe/run command authorizes this stage; avoid redundant prompts.
    key = getpass.getpass("API 密钥（输入隐藏，不保存）：").strip()
    if not key:
        stop("empty API key")
    env = os.environ.copy()
    env["SCENTTRACE_API_KEY"] = key
    return env

def probe(args):
    code = prepared_code(args)
    ensure_cohort_authorization()
    cfg = config(code)
    batch = code / "runs" / "quickstart_probe"
    if not batch.exists():
        choices = []
        with source_jsonl().open(encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                obj = json.loads(line)
                steps = obj.get("events", [])
                if steps and any(e.get("pre", {}).get("status") == "PRESENT" for e in steps):
                    choices.append(((len(steps) > 10, len(steps)), line))
        if not choices: stop("no GUI trajectory in verified data")
        text = min(choices, key=lambda x: x[0])[1]
        source = WORK / "runs" / "inputs" / "probe_one_trajectory.jsonl"
        source.write_text(text, encoding="utf-8")
        prepare_visible(code, cfg, source, batch, 1)
    annotate_visible(code, batch, "单轨迹试跑")

def run_all(args):
    code = prepared_code(args)
    cohort = ensure_cohort_authorization()
    cfg = config(code)
    batch = code / "runs" / "quickstart_full_120"
    if not batch.exists():
        prepare_visible(code, cfg, source_jsonl(), batch, cohort["expected_families"])
    annotate_visible(code, batch, "全量标注")


def prepare_visible(code, cfg, source, batch, groups):
    status = run_command([vpy(), code / "run_batch.py", "prepare", "--config", cfg,
                          "--source", source, "--batch", batch], cwd=code, batch=batch,
                         title="整理标注输入", preparing=True, total_groups=groups)
    if status:
        stop("输入准备失败，请查看上方日志。")


def annotate_visible(code, batch, title):
    status = run_command([vpy(), code / "run_batch.py", "infer", "--batch", batch,
                          "--authorize-inference"], cwd=code, env=key_env(), batch=batch, title=title)
    if status:
        stop("标注进程异常退出，请查看：" + str(batch / "terminal.log"))
    result = check_batch_complete(batch)
    print(f"标注完成：{len(result['completed_groups'])} 个任务。结果目录：{batch}")

def pack(args):
    code = code_location(args)
    runs = code / "runs"
    if not runs.is_dir(): stop("no results to package")
    exports = WORK / "export"
    exports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = exports / ("ScentTrace_E1_results_" + stamp + ".zip")
    files = [f for name in ("quickstart_probe", "quickstart_full_120") for f in (runs / name).rglob("*") if f.is_file() and "__pycache__" not in f.parts]
    if not files: stop("no result files")
    proof = [code / x for x in ("GOAL_CODEBOOK.md", "ALIGNMENT_CODEBOOK.md",
             "OPERATOR_CODEBOOK.md", "schemas.json", "prompts/goals.md",
             "prompts/alignment.md", "prompts/operator.md")]
    proof += [WORK / "data" / "PACKAGE_INDEX.json", ROOT / "docs" / "COHORT_AUTHORIZATION_120.json"]
    manifest = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in files:
            arc = "runs/" + f.relative_to(runs).as_posix()
            z.write(f, arc)
            manifest.append({"path":arc, "bytes":f.stat().st_size, "sha256":hash_file(f)})
        for f in proof:
            if not f.is_file(): continue
            arc = "provenance/" + ("prompts/" if f.parent.name == "prompts" else "") + f.name
            z.write(f, arc)
            manifest.append({"path":arc, "bytes":f.stat().st_size, "sha256":hash_file(f)})
        z.writestr("RESULT_MANIFEST.json", json.dumps({"files":manifest, "created_utc":stamp}, indent=2))
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
    if bad: stop("ZIP CRC failed: " + bad)
    (Path(str(zip_path) + ".sha256")).write_text(hash_file(zip_path) + "  " + zip_path.name + "\n")
    print(f"结果已导出：{zip_path}\n共 {len(manifest)} 个文件；校验文件：{zip_path}.sha256")

def main():
    parser = argparse.ArgumentParser(description="ScentTrace Experiment 1")
    parser.add_argument("--code-dir", help="框架代码目录，默认为 framework/")
    sp = parser.add_subparsers(dest="command", required=True)
    ps = sp.add_parser("setup", help="download / verify / install / test / resolve")
    # Public Drive data needs no remote or Google account.
    ps.add_argument("--local-data", help="已下载的数据包目录")
    sp.add_parser("probe", help="one full short-trajectory real API trial")
    sp.add_parser("run", help="authorized full 120-trajectory batch")
    sp.add_parser("pack", help="results, provenance and hashes into ZIP")
    args = parser.parse_args()
    {"setup":setup, "probe":probe, "run":run_all, "pack":pack}[args.command](args)

if __name__ == "__main__":
    main()
