import yaml
import sys
from pathlib import Path

try:
    workflow = Path(__file__).resolve().parents[1] / "oriexa" / ".github" / "workflows" / "ci-cd.yml"
    with open(workflow, "r", encoding="utf-8") as f:
        yaml.safe_load(f)
    print("YAML is valid")
except Exception as e:
    print(f"YAML Error: {e}")
    sys.exit(1)
