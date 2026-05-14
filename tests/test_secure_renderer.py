import unittest
from pathlib import Path

from secure_renderer import (
    ValidationError,
    build_sandbox_command,
    sanitize_title,
    validate_generated_code,
)


SAFE_SCENE = """
from manim import *
import math
import numpy as np


class DemoScene(Scene):
    def construct(self):
        circle = Circle()
        label = Text(f"pi ~= {math.pi:.2f}")
        dot = Dot(np.array([0, 0, 0]))
        self.add(circle, label, dot)
"""


class SecureRendererTests(unittest.TestCase):
    def test_validate_generated_code_accepts_safe_scene(self):
        scene_name = validate_generated_code(SAFE_SCENE)
        self.assertEqual(scene_name, "DemoScene")

    def test_validate_generated_code_rejects_disallowed_import(self):
        with self.assertRaises(ValidationError):
            validate_generated_code(
                """
import os
from manim import *


class DemoScene(Scene):
    def construct(self):
        self.add(Text("unsafe"))
"""
            )

    def test_validate_generated_code_rejects_top_level_side_effects(self):
        with self.assertRaises(ValidationError):
            validate_generated_code(
                """
from manim import *

print("oops")


class DemoScene(Scene):
    def construct(self):
        self.add(Text("unsafe"))
"""
            )

    def test_validate_generated_code_rejects_builtin_file_access(self):
        with self.assertRaises(ValidationError):
            validate_generated_code(
                """
from manim import *


class DemoScene(Scene):
    def construct(self):
        open("secret.txt", "w")
"""
            )

    def test_build_sandbox_command_uses_isolation_flags(self):
        command = build_sandbox_command(
            runtime="docker",
            work_dir=Path("/tmp/render"),
            script_name="scene.py",
            scene_name="DemoScene",
        )
        self.assertIn("--network", command)
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("ALL", command)

    def test_sanitize_title_normalizes_filename(self):
        self.assertEqual(sanitize_title("  Demo Scene!  "), "Demo_Scene")


if __name__ == "__main__":
    unittest.main()
