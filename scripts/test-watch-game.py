#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline checks for the watcher; no GitHub writes or game downloads."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location("watch_game", Path(__file__).with_name("watch-game.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.game = json.loads(next((watch.ROOT / "data").glob("game-*.json")).read_text())
        self.lib = {"sha256_lib": self.game["mod_lib"]["sha256"], "timestamp": 1}
        self.obb = {"full_sha256": self.game["obb_sha256"], "full_size": self.game["obb_size"],
                    "full": self.game["obb_urls"][0], "time": 1}

    def test_identity_not_timestamp(self):
        self.assertEqual(watch.changes(self.game, self.lib, self.obb), [])

    def test_combined_issue_and_deduplication(self):
        self.lib["sha256_lib"] = "a" * 64
        self.obb["full_sha256"] = "b" * 64
        changed = watch.changes(self.game, self.lib, self.obb)
        responses = [[], {}, {"html_url": "https://github.com/example/repo/issues/1"}]
        with patch.object(watch, "request", side_effect=responses) as req, contextlib.redirect_stdout(io.StringIO()):
            watch.publish("example/repo", "token", self.game, changed, self.lib, self.obb)
        payload = req.call_args.args[1]
        self.assertIn("mod library and OBB data", payload["title"])
        self.assertEqual(payload["labels"], ["game-update"])
        self.assertIn("https://github.com/example/repo/blob/HEAD/docs/", payload["body"])
        with patch.object(watch, "request", return_value=[payload]) as req:
            watch.publish("example/repo", "token", self.game, changed, self.lib, self.obb)
        self.assertEqual(req.call_count, 1)

    def test_pagination_and_partial_deduplication(self):
        self.lib["sha256_lib"] = "a" * 64
        self.obb["full_sha256"] = "b" * 64
        changed = watch.changes(self.game, self.lib, self.obb)
        existing = {"body": watch.marker("Library SHA-256", "a" * 64)}
        exists = urllib.error.HTTPError("https://api.github.com", 422, "exists", {}, None)
        responses = [[{"body": "unrelated"}] * 100, [existing], exists, {"html_url": "created"}]
        with patch.object(watch, "request", side_effect=responses) as req, contextlib.redirect_stdout(io.StringIO()):
            watch.publish("example/repo", "token", self.game, changed, self.lib, self.obb)
        self.assertEqual(req.call_count, 4)
        self.assertNotIn("mod library changed", req.call_args.args[1]["title"])
        self.assertIn("OBB data changed", req.call_args.args[1]["title"])

    def test_offline_and_malformed_skip(self):
        for error in [urllib.error.URLError("offline"), ValueError("bad JSON")]:
            with self.subTest(error=error), patch("sys.argv", ["watch-game.py"]), \
                    patch.object(watch, "request", side_effect=error) as req, \
                    contextlib.redirect_stderr(io.StringIO()) as log:
                watch.main()
                self.assertIn("skipped", log.getvalue())
                self.assertEqual(req.call_count, 1)

    def test_normal_unchanged_is_silent(self):
        with patch("sys.argv", ["watch-game.py"]), \
                patch.object(watch, "request", side_effect=[self.lib, self.obb]) as req, \
                contextlib.redirect_stdout(io.StringIO()) as log:
            watch.main()
        self.assertEqual(log.getvalue(), "")
        self.assertEqual(req.call_count, 2)


if __name__ == "__main__":
    unittest.main()
