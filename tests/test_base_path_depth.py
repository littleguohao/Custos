# -*- coding: utf-8 -*-
"""Guard against BASE path depth regressions in 07_tools subdirectory scripts."""
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "07_tools"


class BasePathDepthTests(unittest.TestCase):
    """Every script in 07_tools/<subdir>/ must resolve BASE to the project root."""

    def test_subdir_scripts_resolve_base_to_project_root(self):
        project_root = TOOLS.parent
        markers = {"00_governance", "01_data", "07_tools"}
        broken = []
        for p in sorted(TOOLS.rglob("*.py")):
            if p.name in ("__init__.py", "conftest.py", "paths.py"):
                continue
            if p.parent == TOOLS:
                continue  # 07_tools/*.py — parent.parent is correct
            text = p.read_text(encoding="utf-8")
            if "parent.parent" in text and "parents[2]" not in text:
                # Check if it's actually a BASE definition
                if "BASE" in text and "parent.parent" in text:
                    broken.append(f"{p.relative_to(TOOLS)}: uses parent.parent (should be parents[2])")
        self.assertEqual(broken, [], f"Scripts with wrong BASE depth:\n" + "\n".join(broken))

    def test_project_root_has_expected_markers(self):
        root = TOOLS.parent
        for marker in ["00_governance", "01_data", "07_tools", "tests"]:
            self.assertTrue((root / marker).exists(), f"Missing project marker: {marker}/")


if __name__ == "__main__":
    unittest.main()
