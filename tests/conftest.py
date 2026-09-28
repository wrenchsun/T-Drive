import sys
from pathlib import Path

# Maya 非依存モジュールを Maya なしで import できるようにする
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maya" / "scripts"))
