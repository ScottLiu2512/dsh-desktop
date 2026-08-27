"""生成应用图标 icon.ico（深蓝底 + 白色 DSH 字母）。

用法：python generate_icon.py
产物：icon.ico（含 256/128/64/48/32/16 多尺寸）
依赖：Pillow（见 requirements-dev.txt）
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
BG = (30, 58, 138, 255)      # 深蓝背景
FG = (255, 255, 255, 255)    # 白色文字
TEXT = "DSH"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",   # Segoe UI Bold
    r"C:\Windows\Fonts\arialbd.ttf",    # Arial Bold
    r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑 Bold
]


def load_font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def build_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(4, size // 5)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)

    font = load_font(int(size * 0.42))
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1]
    draw.text((x, y), TEXT, font=font, fill=FG)
    return img


def main() -> None:
    base = build_icon(SIZE)
    out = Path(__file__).with_name("icon.ico")
    base.save(
        out,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
