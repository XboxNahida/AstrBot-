"""从公开预设补齐首次启动配置，绝不覆盖已有运行文件。"""

import shutil
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    preset = root / "发布预设" / "data"
    runtime = root / "AstrBot-master" / "data"
    if not preset.is_dir():
        raise FileNotFoundError("缺少发布预设/data，请重新解压完整发布包")
    count = 0
    for source in preset.rglob("*"):
        if not source.is_file() or source.is_symlink():
            continue
        target = runtime / source.relative_to(preset)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        count += 1
    print(f"已补齐 {count} 个配置文件，已有配置保持不变。")


if __name__ == "__main__":
    main()
