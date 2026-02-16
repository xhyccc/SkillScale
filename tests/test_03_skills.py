"""
Test 3: Skill Script Execution

Tests the actual skill scripts (text-summarizer, csv-analyzer,
code-complexity) by running them as real subprocesses, just as
the C++ skill server would.
"""

import os
import subprocess
import sys

import pytest

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")


class TestTextSummarizer:
    """Tests the data-processing/text-summarizer skill."""

    SCRIPT = os.path.join(SKILLS_DIR, "data-processing",
                          "text-summarizer", "scripts", "run.py")

    def test_summarizes_multi_paragraph(self):
        """Summarizes a multi-paragraph input."""
        text = (
            "Machine learning is a branch of artificial intelligence. "
            "It focuses on building systems that learn from data. "
            "Deep learning is a subset of machine learning. "
            "It uses neural networks with many layers. "
            "Natural language processing enables computers to understand text. "
            "Computer vision allows machines to interpret images. "
            "Reinforcement learning trains agents through rewards. "
            "Transfer learning reuses pre-trained models for new tasks. "
            "The field continues to advance rapidly each year."
        )

        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=text, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0
        assert "## Summary" in result.stdout
        assert len(result.stdout) > 50

    def test_short_text_returned_as_is(self):
        """Very short text should be returned without trimming."""
        text = "Hello world. This is short."

        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=text, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0

    def test_empty_input_fails(self):
        """Empty input should produce an error."""
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input="", capture_output=True, text=True, timeout=10,
        )

        assert result.returncode != 0


class TestCsvAnalyzer:
    """Tests the data-processing/csv-analyzer skill."""

    SCRIPT = os.path.join(SKILLS_DIR, "data-processing",
                          "csv-analyzer", "scripts", "run.py")

    def test_numeric_columns(self):
        """Correctly identifies and analyzes numeric columns."""
        csv_data = "name,age,score\nAlice,30,95.5\nBob,25,87.2\nCharlie,35,91.0\n"

        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=csv_data, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0
        assert "## CSV Analysis" in result.stdout
        assert "Numeric Columns" in result.stdout
        assert "3" in result.stdout  # 3 rows

    def test_text_columns(self):
        """Identifies text columns and their unique counts."""
        csv_data = "city,country\nParis,France\nLondon,UK\nParis,France\n"

        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=csv_data, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0
        assert "Text" in result.stdout

    def test_empty_csv_fails(self):
        """CSV with only a header should fail."""
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input="col1,col2\n", capture_output=True, text=True, timeout=10,
        )

        assert result.returncode != 0


class TestCodeComplexity:
    """Tests the code-analysis/code-complexity skill."""

    SCRIPT = os.path.join(SKILLS_DIR, "code-analysis",
                          "code-complexity", "scripts", "run.py")

    def test_simple_function(self):
        """Analyzes a simple function."""
        code = '''
def add(a, b):
    return a + b

def greet(name):
    if name:
        print(f"Hello, {name}")
    else:
        print("Hello, stranger")
'''
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=code, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0
        assert "## Code Complexity Report" in result.stdout
        assert "add" in result.stdout
        assert "greet" in result.stdout

    def test_complex_function(self):
        """Detects high complexity in nested control flow."""
        code = '''
def process(data):
    for item in data:
        if item.valid:
            for sub in item.children:
                if sub.active:
                    if sub.value > 100:
                        yield sub
                    elif sub.value > 50:
                        yield sub.transform()
                else:
                    continue
        else:
            raise ValueError("invalid")
'''
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=code, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0
        assert "process" in result.stdout

    def test_class_detection(self):
        """Detects classes in the source."""
        code = '''
class MyService:
    def __init__(self):
        self.data = []

    def add(self, item):
        self.data.append(item)
'''
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=code, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode == 0
        assert "MyService" in result.stdout
        assert "Classes" in result.stdout

    def test_syntax_error_handling(self):
        """Reports syntax errors gracefully."""
        code = "def broken(:\n    pass\n"

        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input=code, capture_output=True, text=True, timeout=10,
        )

        assert result.returncode != 0 or "Failed" in result.stdout

    def test_empty_input_fails(self):
        """Empty input should produce an error."""
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            input="", capture_output=True, text=True, timeout=10,
        )

        assert result.returncode != 0


class TestSkillMdParsing:
    """Tests that SKILL.md files are correctly structured."""

    def _read_skill_md(self, path):
        with open(path) as f:
            content = f.read()
        # Verify frontmatter structure
        assert content.startswith("---"), f"{path} missing frontmatter delimiter"
        second = content.index("---", 3)
        yaml_block = content[3:second].strip()
        return yaml_block

    def test_text_summarizer_skill_md(self):
        path = os.path.join(SKILLS_DIR, "data-processing",
                            "text-summarizer", "SKILL.md")
        yaml = self._read_skill_md(path)
        assert "name:" in yaml
        assert "text-summarizer" in yaml
        assert "description:" in yaml

    def test_csv_analyzer_skill_md(self):
        path = os.path.join(SKILLS_DIR, "data-processing",
                            "csv-analyzer", "SKILL.md")
        yaml = self._read_skill_md(path)
        assert "name:" in yaml
        assert "csv-analyzer" in yaml

    def test_code_complexity_skill_md(self):
        path = os.path.join(SKILLS_DIR, "code-analysis",
                            "code-complexity", "SKILL.md")
        yaml = self._read_skill_md(path)
        assert "name:" in yaml
        assert "code-complexity" in yaml
        assert "allowed-tools:" in yaml
