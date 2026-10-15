"""Offline display tests: real local subprocesses, no provider or downloads."""
import io
import copy
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import e1_progress as p
import e1_quickstart as q


class ProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = q.CODE / "runs" / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=temporary)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / "batch"
        self.folder = self.batch / "000"
        self.folder.mkdir(parents=True)
        self.lines(self.batch / "BATCH_INDEX.jsonl", [dict(group="000", dataset="OM2W",
                   trajectories=["actor-1", "actor-2"], goal_requests=1, alignment_requests=2)])
        self.write(self.folder / "config_snapshot.json", {"execution": {"request_concurrency": 4}})

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def lines(path, rows):
        path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")

    def requests(self, stage, count):
        self.lines(self.folder / f"requests_{stage}.jsonl",
                   [{"request_id": f"{stage}-{i}", "request_sha256": f"sha-{i}"} for i in range(count)])

    def response(self, stage, number, **extra):
        row = dict(request_id=f"{stage}-{number}", request_sha256=f"sha-{number}",
                   elapsed_seconds=8.0, format_retry=0)
        row.update(extra)
        with (self.folder / f"captured_responses_{stage}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")

    def test_tail_ignores_incomplete_lines_and_does_not_duplicate(self):
        path = self.folder / "captured_responses_goals.jsonl"
        data = json.dumps({"request_id": "goals-0", "request_sha256": "sha-0"}).encode()
        path.write_bytes(data[:12])
        files = p.Files()
        self.assertEqual(files.responses(path), [])
        with path.open("ab") as stream:
            stream.write(data[12:] + b"\n")
        self.assertEqual(len(files.responses(path)), 1)
        self.assertEqual(len(files.responses(path)), 1)

    def test_format_retry_does_not_inflate_logical_request_progress(self):
        self.requests("goals", 2)
        view = p.BatchProgress(self.batch)
        self.response("goals", 0)
        self.response("goals", 0, format_retry=1)
        state = view.snapshot()
        self.assertEqual(state["active"]["received"], 1)
        self.assertEqual(state["active"]["total"], 2)
        self.assertEqual(state["active"]["corrections"], 1)
        self.assertEqual(state["success"], 0)

    def test_unbound_capture_is_not_counted(self):
        self.requests("goals", 1)
        view = p.BatchProgress(self.batch)
        self.response("goals", 0, request_sha256="different-request")
        self.assertEqual(view.snapshot()["active"]["received"], 0)

    def test_resume_progress_does_not_treat_old_responses_as_new_timing(self):
        self.requests("goals", 2)
        self.response("goals", 0, elapsed_seconds=9999)
        view = p.BatchProgress(self.batch)
        self.assertFalse(view.samples[(None, "goals")])
        self.assertEqual(view.snapshot()["active"]["received"], 1)
        self.response("goals", 1, elapsed_seconds=3)
        state = view.snapshot()
        self.assertEqual(view.samples[(None, "goals")], [3])
        self.assertEqual(state["active"]["received"], 2)
        self.assertIn("校验", "\n".join(p.frame(state, "试跑")))
        self.assertEqual(state["success"], 0)

    def test_g4_request_total_updates_from_actual_generated_requests(self):
        self.requests("goals", 1)
        self.requests("alignment", 2)
        (self.folder / "results_goals.jsonl").write_text("", encoding="utf-8")
        (self.folder / "results_alignment.jsonl").write_text("", encoding="utf-8")
        view = p.BatchProgress(self.batch)
        self.assertFalse(view.snapshot()["active"]["generated"])
        self.assertNotIn("0/2 已返回", "\n".join(p.frame(view.snapshot(), "测试")))
        self.requests("operator", 5)
        self.assertEqual(view.snapshot()["active"]["total"], 5)
        self.assertTrue(view.snapshot()["active"]["generated"])

    def test_eta_waits_for_real_timing_samples(self):
        self.requests("goals", 3)
        view = p.BatchProgress(self.batch)
        self.assertIsNone(view.snapshot()["active"]["eta"])
        self.response("goals", 0, elapsed_seconds=8)
        self.assertEqual(view.snapshot()["active"]["eta"], 8)
        self.assertIsNone(view.snapshot()["eta"])

    def test_failures_are_separate_from_successful_completion(self):
        view = p.BatchProgress(self.batch)
        view.event("UNCOMPLETED 000")
        state = view.snapshot()
        self.assertEqual((state["processed"], state["success"], state["failed"]), (1, 0, 1))
        self.assertEqual(state["eta"], 0)

    def test_snapshot_only_reads_annotation_artifacts(self):
        self.requests("goals", 1)
        before = {x: x.read_bytes() for x in self.batch.rglob("*") if x.is_file()}
        view = p.BatchProgress(self.batch)
        view.snapshot()
        self.assertEqual(before, {x: x.read_bytes() for x in self.batch.rglob("*") if x.is_file()})

    def test_prepare_does_not_precreate_the_batch_or_print_raw_group_logs(self):
        batch = self.root / "new-batch"
        worker = self.root / "prepare.py"
        worker.write_text("import pathlib,sys\np=pathlib.Path(sys.argv[1])\nassert not p.exists()\n"
                          "p.mkdir()\nprint('PREPARED 000 OM2W 1',flush=True)\n", encoding="utf-8")
        screen = io.StringIO()
        rc = p.run_command([sys.executable, worker, batch], batch=batch, title="整理输入",
                           preparing=True, stream=screen, interval=0.01)
        self.assertEqual(rc, 0)
        self.assertNotIn("PREPARED 000", screen.getvalue())
        self.assertIn("输入准备完成", screen.getvalue())
        self.assertIn("PREPARED 000", (batch.parent / "new-batch.prepare.log").read_text(encoding="utf-8"))

    def test_subprocess_runs_once_and_redacts_key_from_log(self):
        worker = self.root / "worker.py"
        marker = self.root / "executed"
        worker.write_text("import pathlib,sys,os\np=pathlib.Path(sys.argv[1])\n"
                          "with p.open('a') as f:f.write('once\\n')\n"
                          "print(os.environ['SCENTTRACE_API_KEY'])\nprint('COMPLETED 000')\n", encoding="utf-8")
        screen = io.StringIO()
        rc = p.run_command([sys.executable, worker, marker], batch=self.batch, title="试跑",
                           env=dict(os.environ, SCENTTRACE_API_KEY="offline-secret-not-real"),
                           stream=screen, interval=0.01)
        self.assertEqual(rc, 0)
        self.assertEqual(marker.read_text().splitlines(), ["once"])
        self.assertNotIn("COMPLETED 000", screen.getvalue())
        self.assertNotIn("offline-secret-not-real", screen.getvalue())
        log = (self.batch / "terminal.log").read_text(encoding="utf-8")
        self.assertNotIn("offline-secret-not-real", log)
        self.assertIn("[REDACTED]", log)

    def test_process_failure_does_not_print_success(self):
        screen = io.StringIO()
        rc = p.run_command([sys.executable, "-c", "raise SystemExit(3)"], batch=self.batch,
                           title="试跑", stream=screen, interval=0.01)
        self.assertEqual(rc, 3)
        self.assertIn("异常退出", screen.getvalue())

    def test_tty_redraws_in_place(self):
        screen = io.StringIO()
        with mock.patch.object(p, "terminal_live", return_value=True):
            p.run_command([sys.executable, "-c", "import time;time.sleep(0.04)"], batch=self.batch,
                          title="试跑", stream=screen, interval=0.01)
        self.assertIn("\x1b[6F", screen.getvalue())

    def test_partial_checkpoint_is_still_rejected(self):
        self.write(self.batch / "BATCH_EXECUTION_STATUS.json",
                   {"status": "PARTIAL_WITH_ERRORS", "failures": [{"http_status": 503}]})
        with self.assertRaises(SystemExit):
            q.check_batch_complete(self.batch)

    def test_width_and_elapsed_format(self):
        self.assertEqual(p.clip("中文abc", 5), "中文a")
        self.assertEqual(p.duration(3661), "01:01:01")
        self.assertEqual(p.duration(None), "估算中")

    def integration(self, fail=False):
        """Use real native images through the unchanged CLI and a loopback-only API."""
        rows = [json.loads(x) for x in (q.CODE / "runs/inputs/qualified_smoke_four.jsonl").read_text(
            encoding="utf-8-sig").splitlines() if x.strip()]
        trace = copy.deepcopy(next(x for x in rows if x["dataset"] == "OM2W"))
        trace.update(split="MODEL_ANNOTATION_COHORT", trial_role="FULL_TRIAL")
        source = self.root / "source.jsonl"
        self.lines(source, [trace])
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                if fail:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"message":"Offline busy-service fixture"}')
                    return
                payload = json.loads(body["messages"][1]["content"][0]["text"])
                if "goal_catalog" not in payload:
                    result = {"goals": [{"goal_id": "G1", "goal_type": "MIXED",
                        "description": "Offline fixture, no semantic claim", "constraints": [],
                        "completion_condition": "Not evaluated", "depends_on": [],
                        "source_ids": [x["source_id"] for x in payload["source_segments"]]}], "issues": []}
                elif "alignment" not in payload:
                    result = {"ranges": [{"first": payload["target_range"]["first"],
                        "last": payload["target_range"]["last"], "goal_ids": [],
                        "status": "UNRESOLVED", "reason": "Offline fixture only"}]}
                else:
                    result = {"units": [{"unit_index": x["unit_index"], "operator": "UNRESOLVED",
                        "reason": "Offline fixture only"} for x in payload["units"]
                        if payload["target_range"]["first"] <= x["unit_index"] <= payload["target_range"]["last"]]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"id": "OFFLINE-" + str(len(seen)),
                    "model": "OFFLINE_PROGRESS_FIXTURE", "choices": [{"message": {
                        "content": json.dumps(result)}, "finish_reason": "stop"}]}).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cfg = json.loads((q.CODE / "config_api_template.json").read_text(encoding="utf-8-sig"))
        cfg["model"].update(base_url=f"http://127.0.0.1:{server.server_port}/v1",
                            model_id="OFFLINE_PROGRESS_FIXTURE", weight_revision="OFFLINE_FIXTURE")
        cfg["repeat_confirmations"] = []
        config = self.root / "config.json"
        self.write(config, cfg)
        batch = self.root / "integration"
        screen = io.StringIO()
        try:
            with mock.patch.object(q, "vpy", return_value=Path(sys.executable)), mock.patch.object(
                    q, "key_env", return_value=dict(os.environ, SCENTTRACE_API_KEY="offline-key")), contextlib.redirect_stdout(screen):
                q.prepare_visible(q.CODE, config, source, batch, 1)
                if fail:
                    with self.assertRaises(SystemExit):
                        q.annotate_visible(q.CODE, batch, "离线失败测试")
                    self.assertEqual(len(seen), 1)
                    result = json.loads((batch / "BATCH_EXECUTION_STATUS.json").read_text())
                    self.assertEqual(result["status"], "PARTIAL_WITH_ERRORS")
                    self.assertNotIn("标注完成：", screen.getvalue())
                else:
                    q.annotate_visible(q.CODE, batch, "离线三阶段测试")
                    self.assertEqual(len(seen), 3)
                    self.assertTrue(all(x["response_format"]["json_schema"]["strict"] for x in seen))
                    self.assertTrue(any(x["type"] == "image_url" for x in seen[-1]["messages"][1]["content"]))
                    q.annotate_visible(q.CODE, batch, "离线续跑测试")
                    self.assertEqual(len(seen), 3, "Resume must not repeat completed calls")
                    self.assertIn("标注完成：1 个任务", screen.getvalue())
                self.assertNotIn("COMPLETED 000", screen.getvalue())
                self.assertNotIn("offline-key", screen.getvalue())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_real_cli_three_stages_with_native_images_and_no_duplicate_resume(self):
        self.integration()

    def test_real_cli_503_is_partial_and_is_not_retried_by_display(self):
        self.integration(fail=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
