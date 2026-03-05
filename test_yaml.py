import yaml
import sys

try:
    with open(r"f:\Oriexa\Oriexa\.github\workflows\ci-cd.yml", "r", encoding="utf-8") as f:
        yaml.safe_load(f)
    print("YAML is valid")
except Exception as e:
    print(f"YAML Error: {e}")
    sys.exit(1)
