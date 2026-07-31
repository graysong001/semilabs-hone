"""现代 wordmark logo 渲染：真字体、实心、成品感。

设计（放弃字符画/点阵基底）：
- 英文 SEMILABS：Futura Bold + 12° 剪切斜体，青→紫渐变实心大字（主标题）
- 中文 内容工厂：Hiragino Sans GB W6 真字体（解决点阵中文笔画失真的"别扭"），
  字号 ≈ 英文 40%，字距两端对齐英文全宽（letterspaced tagline）
- 无分割线/装饰，层级靠尺寸与色彩：
  W1 中文同渐变 / W2 中文中性银灰（slate-300，经典双色 lockup）

用法: python3 scripts/render_wordmark.py → logo_w1/w2.png
"""
import math

from PIL import Image, ImageDraw, ImageFont

C_LEFT = (34, 211, 238)    # cyan-400
C_RIGHT = (168, 85, 247)   # purple-500
CN_SILVER = (203, 213, 225)  # slate-300
EN_FONT = ("/System/Library/Fonts/Supplemental/Futura.ttc", 2)   # Bold
CN_FONT = ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2)      # W6

SS_EN = 400        # 英文 cap 目标高（4x 高清）
CN_RATIO = 0.40    # 中文行高 / 英文
GAP_RATIO = 0.22   # 行间距 / 英文
SKEW_DEG = 12      # 斜体角度（现代 italic 惯例）


def gradient(w, h, x0, x1):
    """横向渐变条：t 按全图宽 [x0,x1] 归一。"""
    g = Image.new("RGBA", (w, h))
    px = g.load()
    for x in range(w):
        t = min(1.0, max(0.0, (x0 + x) / x1))
        c = tuple(round(a + (b - a) * t) for a, b in zip(C_LEFT, C_RIGHT))
        for y in range(h):
            px[x, y] = c + (255,)
    return g


def text_mask(text, font_spec, target_h, tracking=0):
    """文字 → L 蒙版，高度归一到 target_h；tracking 为字间额外像素。"""
    path, idx = font_spec
    probe = ImageFont.truetype(path, 400, index=idx)
    img = Image.new("L", (400 * (len(text) + 2), 800), 0)
    d = ImageDraw.Draw(img)
    x = 100
    for ch in text:
        d.text((x, 200), ch, font=probe, fill=255)
        x += d.textlength(ch, font=probe) + tracking
    bb = img.getbbox()
    crop = img.crop(bb)
    ratio = target_h / crop.height
    return crop.resize((max(1, round(crop.width * ratio)), target_h), Image.LANCZOS)


def shear(mask, deg):
    """右倾剪切：顶部右移 tan(deg)*h。"""
    k = math.tan(math.radians(deg))
    w, h = mask.size
    extra = int(k * h) + 2
    return mask.transform((w + extra, h), Image.AFFINE,
                          (1, k, -k * h, 0, 1, 0), resample=Image.BICUBIC)


def render(cn_color=None):
    en = shear(text_mask("SEMILABS", EN_FONT, SS_EN), SKEW_DEG)
    W = en.width
    cn_h = int(SS_EN * CN_RATIO)
    gap = int(SS_EN * GAP_RATIO)

    # 中文：逐字实渲后摆位，精确两端对齐英文全宽
    chars = [text_mask(ch, CN_FONT, cn_h) for ch in "内容工厂"]
    total_w = sum(c.width for c in chars)
    gap_x = (W - total_w) / 3
    cn_row = Image.new("L", (W, cn_h), 0)
    x = 0.0
    for c in chars:
        cn_row.paste(c, (round(x), 0))
        x += c.width + gap_x

    H = SS_EN + gap + cn_h
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad_full = gradient(W, SS_EN, 0, W)
    out.paste(grad_full, (0, 0), en)
    if cn_color:
        cn_img = Image.new("RGBA", (W, cn_h), cn_color + (255,))
    else:
        cn_img = gradient(W, cn_h, 0, W)
    out.paste(cn_img, (0, SS_EN + gap), cn_row)
    return out


if __name__ == "__main__":
    for name, kw in {"w1": dict(cn_color=None),
                     "w2": dict(cn_color=CN_SILVER)}.items():
        img = render(**kw)
        p = f"semilabs_hone/core/ui/static/img/logo_{name}.png"
        img.save(p)
        print("saved", p, img.size)
