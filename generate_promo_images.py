"""生成 DSH Desktop 宣传素材（少数派投稿配图）。

输出两张图到 promo/ 目录：
  1. promo/cover.png        — 标题图 / 封面（1600x900）
  2. promo/flow-compare.png — 启动流程对比图（1600x600）

依赖：Pillow（见 requirements-dev.txt）、微软雅黑字体（系统自带）。
素材源：release/screenshot.png（DSH Desktop 真实运行截图，需先手动放好；release/ 不入库）。

用法：
    python generate_promo_images.py
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---- 路径配置 ----
ROOT = Path(__file__).parent
SRC_SHOT = ROOT / "release" / "screenshot.png"
PROMO_DIR = ROOT / "promo"
OUT_COVER = PROMO_DIR / "cover.png"
OUT_FLOW = PROMO_DIR / "flow-compare.png"


def _read_version() -> str:
    """从 dsh_gui/__init__.py 读取版本号，避免在多处手动同步。"""
    init_file = ROOT / "dsh_gui" / "__init__.py"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_file.read_text(encoding="utf-8"))
    return match.group(1) if match else "unknown"

# ---- 字体路径（Windows 系统自带）----
FONT_BLACK = "C:/Windows/Fonts/seguibl.ttf"       # Segoe UI Black，英文标题
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"          # 微软雅黑粗，中文标题
FONT_REG = "C:/Windows/Fonts/msyh.ttc"             # 微软雅黑，中文正文


# ============================================================
# 通用辅助
# ============================================================
def load_font(path: str, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    """加载字体，ttc 需指定 index。"""
    return ImageFont.truetype(path, size, index=index)


def draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    radius: int,
    fill: tuple[int, int, int, int] | None = None,
    outline: tuple[int, int, int, int] | None = None,
    width: int = 1,
) -> None:
    """画圆角矩形（兼容 Pillow 老版本）。"""
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    except AttributeError:
        # 老版本 fallback：用 4 个圆 + 2 个矩形拼接
        x0, y0, x1, y1 = box
        draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
        draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
        for cx, cy in [(x0 + radius, y0 + radius), (x1 - radius, y0 + radius),
                       (x0 + radius, y1 - radius), (x1 - radius, y1 - radius)]:
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=fill)


def add_drop_shadow(
    img: Image.Image,
    padding: int = 30,
    blur: int = 20,
    shadow_color: tuple[int, int, int] = (0, 0, 0),
    opacity: int = 140,
) -> Image.Image:
    """给图片加投影，返回更大的画布。"""
    w, h = img.size
    canvas = Image.new("RGBA", (w + padding * 2, h + padding * 2), (0, 0, 0, 0))
    # 影子层
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rectangle([padding, padding, padding + w, padding + h], fill=shadow_color + (opacity,))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas = Image.alpha_composite(canvas, shadow)
    # 主图贴到画布上
    canvas.paste(img, (padding, padding), img if img.mode == "RGBA" else None)
    return canvas


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ============================================================
# 图 1：标题图 / 封面（1600x900）
# ============================================================
def make_cover() -> None:
    W, H = 1600, 900
    # 渐变背景：#0D1117 → #161B22 → #0D1117（左右深、中间稍亮）
    bg = Image.new("RGB", (W, H), hex_to_rgb("#0D1117"))
    px = bg.load()
    c_start = hex_to_rgb("#0D1117")
    c_mid = hex_to_rgb("#1C2128")
    for x in range(W):
        if x < W // 2:
            t = x / (W // 2)
            r = int(c_start[0] + (c_mid[0] - c_start[0]) * t)
            g = int(c_start[1] + (c_mid[1] - c_start[1]) * t)
            b = int(c_start[2] + (c_mid[2] - c_start[2]) * t)
        else:
            t = (x - W // 2) / (W // 2)
            r = int(c_mid[0] + (c_start[0] - c_mid[0]) * t)
            g = int(c_mid[1] + (c_start[1] - c_mid[1]) * t)
            b = int(c_mid[2] + (c_start[2] - c_mid[2]) * t)
        for y in range(H):
            px[x, y] = (r, g, b)

    bg = bg.convert("RGBA")
    draw = ImageDraw.Draw(bg)

    # 顶部装饰条：DeepSeek 蓝细线
    draw.rectangle([0, 0, W, 6], fill=hex_to_rgb("#4D6BFE") + (255,))

    # ---- 左侧：截图区（占左 1/3，约 533px）----
    if SRC_SHOT.exists():
        shot = Image.open(SRC_SHOT).convert("RGBA")
        # 目标显示尺寸：左侧留 60px 边距，竖直居中
        max_w = 560
        max_h = 620
        sw, sh = shot.size
        scale = min(max_w / sw, max_h / sh)
        new_w, new_h = int(sw * scale), int(sh * scale)
        shot_resized = shot.resize((new_w, new_h), Image.LANCZOS)
        # 加投影
        shot_shadowed = add_drop_shadow(shot_resized, padding=24, blur=18, opacity=160)
        # 贴到画布左侧居中
        sx = 60 - 24  # 减去 padding 让截图主体左边距 60
        sy = (H - shot_shadowed.size[1]) // 2
        bg.alpha_composite(shot_shadowed, (sx, sy))

    # ---- 右侧：标题区 ----
    # 大标题 DSH Desktop
    f_title = load_font(FONT_BLACK, 110)
    f_sub_cn = load_font(FONT_BOLD, 38, index=0)
    f_sub_en = load_font("C:/Windows/Fonts/segoeui.ttf", 26)
    f_tag = load_font(FONT_BOLD, 24, index=0)

    # 标题位置
    tx = 700
    ty = 280
    draw.text((tx, ty), "DSH Desktop", font=f_title, fill=(255, 255, 255, 255))
    # 副标题
    draw.text((tx + 4, ty + 130), "DeepSeek Harness 的原生桌面壳", font=f_sub_cn, fill=(180, 190, 210, 255))
    # 英文小标
    draw.text((tx + 4, ty + 190), "One-click desktop client for dsh web", font=f_sub_en, fill=(120, 130, 150, 255))

    # ---- 装饰：右下角 DeepSeek 文字水印 ----
    f_water = load_font(FONT_BOLD, 28, index=0)
    draw.text((W - 240, H - 60), "DeepSeek", font=f_water, fill=(80, 90, 110, 180))

    # ---- 左下角版本标签 ----
    tag_x, tag_y = 60, H - 80
    draw_rounded_rect(draw, (tag_x, tag_y, tag_x + 220, tag_y + 44), 22,
                      fill=hex_to_rgb("#4D6BFE") + (255,))
    draw.text((tag_x + 20, tag_y + 8), f"v{_read_version()}  ·  Windows", font=f_tag, fill=(255, 255, 255, 255))

    PROMO_DIR.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(OUT_COVER, "PNG")
    print(f"[cover] 已保存：{OUT_COVER}  尺寸 {bg.size}")


# ============================================================
# 图 6：启动流程对比图（1600x600）
# ============================================================
def make_flow_compare() -> None:
    W, H = 1600, 600
    # 背景：纯深色
    bg = Image.new("RGB", (W, H), hex_to_rgb("#0D1117"))
    draw = ImageDraw.Draw(bg)

    # 顶部装饰条
    draw.rectangle([0, 0, W, 6], fill=hex_to_rgb("#4D6BFE"))

    # 标题
    f_title = load_font(FONT_BOLD, 32, index=0)
    f_step = load_font(FONT_BOLD, 22, index=0)
    f_desc = load_font(FONT_REG, 18, index=0)
    f_arrow = load_font(FONT_BOLD, 24, index=0)
    f_label = load_font(FONT_BOLD, 26, index=0)

    draw.text((W // 2 - 200, 30), "启动 DSH 的两种方式", font=f_title, fill=(220, 225, 235, 255))

    # ---- 三栏布局 ----
    # 左栏：传统方式（灰调）
    LEFT_W = 560
    left_x0 = 40
    left_x1 = left_x0 + LEFT_W
    left_y0 = 100
    left_y1 = H - 60
    draw_rounded_rect(draw, (left_x0, left_y0, left_x1, left_y1), 16,
                      fill=(26, 31, 40, 255), outline=(60, 66, 78, 255), width=2)
    draw.text((left_x0 + 24, left_y0 + 16), "传统方式", font=f_label, fill=(140, 150, 165, 255))

    # 左栏 5 步
    left_steps = [
        "① 打开终端 / CMD",
        "② 输入 dsh web",
        "③ 等待并复制 URL",
        "④ 打开浏览器",
        "⑤ 粘贴并访问",
    ]
    step_box_h = 56
    gap = 14
    cur_y = left_y0 + 70
    for step in left_steps:
        draw_rounded_rect(draw, (left_x0 + 24, cur_y, left_x1 - 24, cur_y + step_box_h), 10,
                          fill=(40, 45, 55, 255))
        draw.text((left_x0 + 38, cur_y + 15), step, font=f_step, fill=(180, 188, 200, 255))
        cur_y += step_box_h + gap

    # ---- 中间：大箭头 "化繁为简" ----
    mid_cx = (left_x1 + (W - LEFT_W - 80)) // 2
    mid_y = (H // 2) - 10
    # 箭头：三角形 + 矩形
    arrow_color = hex_to_rgb("#4D6BFE")
    # 矩形身
    draw.rectangle([mid_cx - 70, mid_y - 12, mid_cx + 30, mid_y + 12], fill=arrow_color)
    # 三角头
    draw.polygon([(mid_cx + 30, mid_y - 28), (mid_cx + 30, mid_y + 28), (mid_cx + 70, mid_y)],
                 fill=arrow_color)
    # 文字
    draw.text((mid_cx - 50, mid_y - 60), "化繁为简", font=f_arrow, fill=(220, 230, 245, 255))

    # ---- 右栏：DSH Desktop（蓝调）----
    right_x0 = W - 40 - LEFT_W
    right_x1 = right_x0 + LEFT_W
    right_y0 = 100
    right_y1 = H - 60
    draw_rounded_rect(draw, (right_x0, right_y0, right_x1, right_y1), 16,
                      fill=(20, 32, 60, 255), outline=(77, 107, 254, 255), width=2)
    draw.text((right_x0 + 24, right_y0 + 16), "DSH Desktop", font=f_label,
              fill=(140, 170, 255, 255))

    # 右栏 2 步
    right_steps = [
        "① 双击 DSH-Desktop 图标",
        "② 自动加载 Harness 界面",
    ]
    cur_y = right_y0 + 120
    for step in right_steps:
        draw_rounded_rect(draw, (right_x0 + 24, cur_y, right_x1 - 24, cur_y + step_box_h + 8), 10,
                          fill=(30, 50, 100, 255), outline=(77, 107, 254, 180), width=1)
        draw.text((right_x0 + 38, cur_y + 19), step, font=f_step, fill=(220, 230, 255, 255))
        cur_y += step_box_h + 8 + gap

    # 右栏底部装饰：节省步数标签
    draw_rounded_rect(draw, (right_x0 + 24, right_y1 - 60, right_x0 + 220, right_y1 - 22), 14,
                      fill=hex_to_rgb("#4D6BFE") + (255,))
    draw.text((right_x0 + 38, right_y1 - 56), "节省 3 步", font=f_step, fill=(255, 255, 255, 255))

    PROMO_DIR.mkdir(parents=True, exist_ok=True)
    bg.save(OUT_FLOW, "PNG")
    print(f"[flow] 已保存：{OUT_FLOW}  尺寸 {bg.size}")


if __name__ == "__main__":
    print(f"源截图：{SRC_SHOT}  存在={SRC_SHOT.exists()}")
    make_cover()
    make_flow_compare()
    print("\n完成。两张图已生成到 promo/ 目录。")
