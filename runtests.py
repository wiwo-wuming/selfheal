"""Helper to run pytest and print results."""
import subprocess
import sys
from pathlib import Path

project_dir = Path(__file__).parent
result = subprocess.run(
    [sys.executable, "-m", "pytest", str(project_dir / "tests"), "-v", "--tb=short", "-p", "no:warnings"],
    capture_output=True,
    text=True,
    cwd=str(project_dir),
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:1000])
sys.exit(result.returncode)
