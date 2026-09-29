import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "calibrate_repetition.py"


def para(i, words=40):
    return " ".join(f"w{i}x{j}" for j in range(words))


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_reports_each_directory_and_names_documents_above_the_threshold(tmp_path):
    real, padded = tmp_path / "real", tmp_path / "padded"
    real.mkdir()
    padded.mkdir()
    body = [para(i) for i in range(60)]
    (real / "a.txt").write_text("\n\n".join(body))
    (padded / "b.md").write_text("\n\n".join(body + body[:30]))
    result = run(real, padded)
    assert result.returncode == 0, result.stderr
    assert "n=1" in result.stdout and "above 0.1: 0" in result.stdout
    assert "above 0.1: 1" in result.stdout and "b.md" in result.stdout


def test_refuses_a_path_that_is_not_a_directory(tmp_path):
    result = run(tmp_path / "missing")
    assert result.returncode == 2
    assert "not a directory" in result.stderr
