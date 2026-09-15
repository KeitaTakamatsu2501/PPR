"""独立性の検証。通常起動でJTS/MyFriends/音声/ネットワークを呼ばない。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")

FORBIDDEN_PREFIXES = (
    "offline_generation", "dual_voice_chat_core", "character_profiles",
    "high_quality_character_profiles", "app_paths", "service_database",
    "bedrock_mantle_client", "language_model_provider", "myfriends_compatibility",
    "requests", "botocore", "boto3", "urllib.request", "http.client", "socket",
)

PROBE = """
import sys, json
sys.path.insert(0, %r)
import ppr, ppr.domain, ppr.storage, ppr.revisions, ppr.config, ppr.templates, ppr.assets
import ppr.__main__
import ppr.adapters.jts.importer
print(json.dumps(sorted(sys.modules)))
"""


class ImportBoundary(unittest.TestCase):
    def test_core_modules_pull_in_no_jts_or_network_dependency(self):
        out = subprocess.run(
            [sys.executable, "-c", PROBE % SRC],
            capture_output=True, text=True, check=True,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
        )
        modules = set(__import__("json").loads(out.stdout))
        leaked = sorted(m for m in modules if m in FORBIDDEN_PREFIXES)
        self.assertEqual(leaked, [], f"通常importで持ち込まれてはいけないモジュール: {leaked}")

    def test_cli_runs_with_an_empty_data_dir_and_no_jts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-m", "ppr", "--offline", "--data-dir", tmp, "list"],
                capture_output=True, text=True,
                env={"PYTHONPATH": SRC, "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("人格がありません", result.stdout)

    def test_bundled_template_is_readable_without_jts(self):
        from ppr.templates import load_bundled_template

        template = load_bundled_template()
        self.assertEqual(len(template.categories), 194)
        self.assertEqual(template.duplicate_categories(), ())
        self.assertNotIn("恒常的な価値境界", template.categories)


if __name__ == "__main__":
    unittest.main()
