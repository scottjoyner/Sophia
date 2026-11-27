"""Hermetic R43 guard test for ``voice_agent.auth.challenge``.

The target module is loaded by absolute path (override with CHALLENGE_MODULE)
so the *same* byte-for-byte test file runs against a pristine copy
(fail-before) and the fixed worktree (pass-after).  Uses only the stdlib;
no FastAPI app, Neo4j, model, network or live state is involved.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "voice_agent"
    / "auth"
    / "challenge.py"
)


class _GuardAbsent(Exception):
    """Sentinel used when the pristine module has no typed guard yet."""


def _load_module():
    path = os.environ.get("CHALLENGE_MODULE") or str(DEFAULT_PATH)
    spec = importlib.util.spec_from_file_location("challenge_r43_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ChallengeFailClosedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()
        cls.guard_error = getattr(
            cls.mod, "ChallengePhraseUnavailableError", _GuardAbsent
        )

    def test_no_configured_path_keeps_public_defaults(self):
        self.assertEqual(self.mod.load_phrases(None), self.mod.DEFAULT_PHRASES)

    def test_custom_file_is_loaded_verbatim(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "phrases.txt"
            p.write_text("  alpha phrase  \n\nbeta phrase\n", encoding="utf-8")
            self.assertEqual(
                self.mod.load_phrases(str(p)), ["alpha phrase", "beta phrase"]
            )

    def test_missing_configured_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nonexistent.txt"
            with self.assertRaises(self.guard_error):
                self.mod.load_phrases(str(p))

    def test_unreadable_configured_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a-directory"
            p.mkdir()
            with self.assertRaises(self.guard_error):
                self.mod.load_phrases(str(p))

    def test_empty_configured_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.txt"
            p.write_text("  \n\n\t\n", encoding="utf-8")
            with self.assertRaises(self.guard_error):
                self.mod.load_phrases(str(p))

    def test_random_phrase_uses_configured_phrases(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "phrases.txt"
            p.write_text("only phrase\n", encoding="utf-8")
            self.assertEqual(self.mod.random_phrase(str(p)), "only phrase")

    def test_random_phrase_none_unchanged(self):
        self.assertIn(self.mod.random_phrase(None), self.mod.DEFAULT_PHRASES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
