from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from markcontrol.gui.icons import logo


def main():
    image = logo()
    image.save(ROOT / "assets/icon.png")
    image.save(ROOT / "assets/icon.ico", sizes=[(n, n) for n in (16, 20, 24, 32, 48, 64, 128, 256)])


if __name__ == "__main__":
    main()
