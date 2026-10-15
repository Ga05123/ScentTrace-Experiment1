"""Read-only terminal progress for the existing annotation subprocess."""
import collections
import ctypes
import json
import math
import os
from pathlib import Path
import queue
import shutil
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime


STAGES = ("goals", "alignment", "operator")
LABELS = {"goals": "G2 目标解析", "alignment": "G3 连续对齐", "operator": "G4 行为标注"}


def duration(seconds):
    if seconds is None:
        return "估算中"
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class Files:
    """Cache fixed requests; tail only complete, appended response lines."""
    def __init__(self):
        self.cache = {}
        self.tails = {}

    def json(self, path):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None  # A writer may currently be replacing this display-only file.

    def requests(self, path):
        try:
            signature = (path.stat().st_mtime_ns, path.stat().st_size)
        except FileNotFoundError:
            return None
        if self.cache.get(path, (None,))[0] != signature:
            try:
                rows = [json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
            except json.JSONDecodeError:
                return None
            self.cache[path] = (signature, {x["request_id"]: x["request_sha256"] for x in rows})
        return self.cache[path][1]

    def responses(self, path):
        state = self.tails.setdefault(path, {"offset": 0, "rows": []})
        try:
            if path.stat().st_size < state["offset"]:
                state.update(offset=0, rows=[])
            with path.open("rb") as stream:
                stream.seek(state["offset"])
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        break
                    state["rows"].append({k: row.get(k) for k in (
                        "request_id", "request_sha256", "elapsed_seconds", "format_retry")})
                    state["offset"] += len(line)
        except FileNotFoundError:
            pass
        return state["rows"]


class BatchProgress:
    def __init__(self, batch, clock=time.monotonic):
        self.batch = Path(batch)
        self.clock = clock
        self.started = clock()
        self.files = Files()
        self.finished = {}
        self.prepared = set()
        self.baseline = {}
        self.samples = collections.defaultdict(list)
        self.snapshot()  # Existing responses are progress, never new-run timing samples.

    def event(self, line):
        parts = line.split()
        if len(parts) > 1:
            if parts[0] == "PREPARED":
                self.prepared.add(parts[1])
            if parts[0] in {"COMPLETED", "UNCOMPLETED"}:
                self.finished[parts[1]] = parts[0] == "COMPLETED"

    def snapshot(self):
        index = self.batch / "BATCH_INDEX.jsonl"
        try:
            groups = [json.loads(x) for x in index.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        except (FileNotFoundError, json.JSONDecodeError):
            groups = []
        active = None
        remaining = []
        for group in groups:
            folder = self.batch / group["group"]
            cfg = self.files.json(folder / "config_snapshot.json") or {}
            workers = cfg.get("execution", {}).get("request_concurrency", 4)
            for stage in STAGES:
                requests = self.files.requests(folder / f"requests_{stage}.jsonl")
                rows = self.files.responses(folder / f"captured_responses_{stage}.jsonl")
                bound = [x for x in rows if requests is not None and
                         requests.get(x["request_id"]) == x["request_sha256"]]
                seen = {x["request_id"] for x in bound}
                key = (group["group"], stage)
                if key not in self.baseline:
                    self.baseline[key] = len(bound)
                fresh = bound[self.baseline[key]:]
                self.baseline[key] = len(bound)
                for row in fresh:
                    latency = row.get("elapsed_seconds")
                    if isinstance(latency, (int, float)) and math.isfinite(latency) and latency > 0:
                        self.samples[(group["dataset"], stage)].append(latency)
                        self.samples[(None, stage)].append(latency)
                complete = (folder / f"results_{stage}.jsonl").exists()
                total = len(requests) if requests is not None else (
                    group.get("goal_requests", 1) if stage == "goals" else group.get("alignment_requests", 0))
                if group["group"] in self.finished or complete:
                    continue
                pending = max(0, total - len(seen))
                samples = self.samples[(group["dataset"], stage)] or self.samples[(None, stage)]
                estimate = (math.ceil(pending / min(workers, max(1, total))) *
                            statistics.median(samples[-20:])) if samples else None
                remaining.append(estimate)
                if active is None:
                    active = dict(dataset=group["dataset"], trajectories=len(group.get("trajectories", [])),
                                  stage=stage, received=len(seen), total=total,
                                  generated=requests is not None, eta=estimate,
                                  corrections=sum(bool(x.get("format_retry")) for x in bound))
        total_eta = sum(remaining) if remaining and all(x is not None for x in remaining) else None
        if groups and len(self.finished) == len(groups):
            total_eta = 0
        successes = sum(self.finished.values())
        return dict(groups=len(groups), trajectories=sum(len(x.get("trajectories", [])) for x in groups),
                    processed=len(self.finished), success=successes, failed=len(self.finished) - successes,
                    active=active, elapsed=self.clock() - self.started, eta=total_eta)


def bar(done, total):
    filled = int(18 * min(done, total) / total) if total else 0
    return "[" + "=" * filled + "." * (18 - filled) + "]"


def frame(state, title):
    active = state["active"]
    percent = int(100 * state["processed"] / state["groups"]) if state["groups"] else 0
    rows = [f"ScentTrace | {title} | {state['trajectories']} 条轨迹",
            f"总体 {bar(state['processed'], state['groups'])} {percent}% {state['processed']}/{state['groups']} 个任务已处理"
            f"  完成 {state['success']}  未完成 {state['failed']}"]
    if active:
        rows.append(f"当前 {active['dataset']} | {active['trajectories']} 条轨迹 | {LABELS[active['stage']]}")
        message = "生成请求中" if not active["generated"] else (
            "校验 / 格式修正中" if active["received"] == active["total"] else "等待模型返回")
        rows.append(f"响应 {bar(active['received'], active['total'])} {active['received']}/{active['total']} 已返回"
                    f"  {message}" if active["generated"] else "正在生成本阶段请求，实际总数尚未确定")
        rows.append(f"本阶段剩余约 {duration(active['eta'] if active['generated'] else None)}"
                    f" | 格式修正 {active['corrections']} 次")
    else:
        rows.extend(["正在核对最终批次状态", "", ""])
    finish = datetime.fromtimestamp(time.time() + state["eta"]).strftime("%H:%M") if state["eta"] is not None else "--"
    rows.append(f"已用 {duration(state['elapsed'])} | 总剩余约 {duration(state['eta'])} | 预计结束 {finish}")
    return rows


def terminal_live(stream):
    if not stream.isatty():
        return False
    if os.name == "nt":
        mode = ctypes.c_ulong()
        ctypes.windll.kernel32.GetStdHandle.restype = ctypes.c_void_p
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 4))
    return True


def clip(text, width):
    result, used = [], 0
    for char in text:
        used += 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used > width:
            break
        result.append(char)
    return "".join(result)


def run_command(args, *, batch, title, cwd=None, env=None, preparing=False, total_groups=1,
                stream=None, interval=1):
    """Run the same command once; all observation is read-only."""
    stream = sys.stdout if stream is None else stream
    batch = Path(batch)
    log = batch.parent / (batch.name + ".prepare.log") if preparing else batch / "terminal.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    events = queue.Queue()
    view = BatchProgress(batch)
    live = terminal_live(stream)
    child_env = dict(os.environ if env is None else env, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    secret = child_env.get("SCENTTRACE_API_KEY", "")
    stream.write(f"{title}。完整日志：{log}\n")
    if not preparing:
        stream.write("响应进度不等于校验通过；总体 ETA 包含尚未生成的 G4 请求估计。\n")
    stream.flush()
    with log.open("a", encoding="utf-8") as output:
        process = subprocess.Popen([str(x) for x in args], cwd=cwd, env=child_env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace")

        def reader():
            for line in process.stdout:
                line = line.replace(secret, "[REDACTED]") if secret else line
                output.write(line)
                output.flush()
                events.put(line)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        printed, last_print, previous_key = 0, -15, None
        try:
            while True:
                while not events.empty():
                    view.event(events.get_nowait())
                state = view.snapshot()
                if preparing:
                    rows = [title, f"{bar(len(view.prepared), total_groups)} 已整理 {len(view.prepared)}/{total_groups} 个任务",
                            f"已用 {duration(state['elapsed'])}"]
                else:
                    rows = frame(state, title)
                key = (state["processed"], str(state["active"]), len(view.prepared))
                now = time.monotonic()
                done = process.poll() is not None and not thread.is_alive()
                if live or done or now - last_print >= 15 or key != previous_key:
                    if live and printed:
                        stream.write(f"\x1b[{printed}F")
                    width = max(1, shutil.get_terminal_size().columns - 1)
                    for row in rows:
                        stream.write(("\x1b[2K" if live else "") + clip(row, width) + "\n")
                    stream.flush()
                    printed, last_print, previous_key = len(rows), now, key
                if done:
                    break
                time.sleep(interval)
        except BaseException:
            if process.poll() is None:
                process.terminate()
            process.wait()
            thread.join()
            raise
        thread.join()
        process.stdout.close()
    stream.write((f"进程异常退出（退出码 {process.returncode}）" if process.returncode else
                  "输入准备完成" if preparing else
                  f"本轮结束：完成 {state['success']} 个任务，未完成 {state['failed']} 个任务" if not preparing else
                  "输入准备未完成") + f"。日志：{log}\n")
    stream.flush()
    return process.returncode
