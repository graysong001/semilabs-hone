"""基线 logo 视觉层级重构补丁。

设计诊断（用户"总感觉哪里有问题"的根源）：
- 虚实倒挂：英文空心线框轻、中文实心方块重 → 副标题压过主标题
- 层级缺失：中文行高 89% ≈ 英文，主副几乎等大

处理：
1. 英文带：slant 字符画空腔填入 40% 暗调渐变 → 亮描边 + 暗芯
   霓虹灯管效果，主标题视觉重量回归（外部区域 flood fill 判定空腔）
2. 中文带：W6 粗体 20×16 点阵 + SKEW 斜体，行高降到 64px（≈英文 52%），
   cell 扁砖化（宽=1.4×高）→ 宽扁 tagline，四字仍两端对齐全宽
3. 英中之间 hairline 中性灰细线（结构件用中性色，不与品牌渐变竞争）
4. 画布重排：123 + 20 + 3 + 18 + 64 = 228 高

用法: python3 scripts/patch_logo_cn.py（幂等：英文带总是取自基线备份）
"""
from collections import deque

from PIL import Image, ImageDraw, ImageFont

SRC = "semilabs_hone/core/ui/static/img/logo.png"
BASELINE = "semilabs_hone/core/ui/static/img/logo_baseline.png"  # 英文带来源
C_LEFT = (34, 211, 238)    # cyan-400
C_RIGHT = (168, 85, 247)   # purple-500
CN_FONT = ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2)  # W6 粗体

GRID_COLS, GRID_ROWS = 20, 16   # 20 列：单字横向拉宽
THRESH = 60                     # 覆盖率阈值（0~255），放宽 → 加粗
SKEW = 0.35                     # 每行右偏 cell 数，呼应英文 slant 斜体
EN_H = 123                      # 英文带高
GAP1 = 24                       # 英文 → 分割线间距（留白即分割）
RULE_H = 5                      # 分割线高（44px 显示≈ 1px hairline）
RULE_COLOR = (100, 116, 139)    # slate-500 中性灰，与 UI border 同语系，不抢渐变
GAP2 = 20                       # 分割线 → 中文间距
CN_H = 64                       # 中文带高（≈ 英文 52%，宽扁副标题）
CELL_AR = 1.4                   # cell 宽高比：扁砖化 → 更宽更扁
FILL_DIM = 0.40                 # 英文空腔暗芯亮度（0=黑，1=描边同亮）
SS = 4                          # 超采样倍率


def lerp(t):
    return tuple(round(a + (b - a) * t) for a, b in zip(C_LEFT, C_RIGHT))


def char_cells(ch):
    """单字 → 20×16 bool 网格（W6 渲染 → LANCZOS 下采样 → 阈值）。"""
    size = 256
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(CN_FONT[0], int(size * 0.78), index=CN_FONT[1])
    d.text((size * 0.1, size * 0.1), ch, font=f, fill=255)
    bb = d.textbbox((size * 0.1, size * 0.1), ch, font=f)
    crop = img.crop(bb)
    small = crop.resize((GRID_COLS, GRID_ROWS), Image.LANCZOS)
    px = small.load()
    return [[px[x, y] >= THRESH for x in range(GRID_COLS)] for y in range(GRID_ROWS)]


def fill_en_cavity(en):
    """slant 字符画空腔填暗调渐变：flood fill 标记外部，其余透明区即空腔。"""
    W, H = en.size
    px = en.load()
    outside = [[False] * W for _ in range(H)]
    q = deque()
    for x in range(W):                      # 四周边界的透明像素为种子
        for y in (0, H - 1):
            if px[x, y][3] == 0 and not outside[y][x]:
                outside[y][x] = True
                q.append((x, y))
    for y in range(H):
        for x in (0, W - 1):
            if px[x, y][3] == 0 and not outside[y][x]:
                outside[y][x] = True
                q.append((x, y))
    while q:
        x, y = q.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < W and 0 <= ny < H and not outside[ny][nx] and px[nx, ny][3] == 0:
                outside[ny][nx] = True
                q.append((nx, ny))
    for y in range(H):
        for x in range(W):
            if px[x, y][3] == 0 and not outside[y][x]:   # 空腔 → 暗芯
                c = lerp(x / W)
                px[x, y] = tuple(round(v * FILL_DIM) for v in c) + (255,)
    return en


def main():
    base = Image.open(BASELINE).convert("RGBA")
    W = base.width
    en = fill_en_cavity(base.crop((0, 0, W, EN_H)))

    H_out = EN_H + GAP1 + RULE_H + GAP2 + CN_H
    out = Image.new("RGBA", (W, H_out), (0, 0, 0, 0))
    out.paste(en, (0, 0))

    # 分割线：中性灰 hairline，只做结构暗示，不与品牌渐变竞争
    dline = ImageDraw.Draw(out)
    y_rule = EN_H + GAP1
    dline.rectangle([0, y_rule, W - 1, y_rule + RULE_H - 1], fill=RULE_COLOR + (255,))

    cell = CN_H / GRID_ROWS                    # 纵向 cell 高
    cw = cell * CELL_AR                        # 横向 cell 宽（扁砖）
    shear_w = SKEW * (GRID_ROWS - 1) * cw      # 斜体每字额外占宽
    char_w = GRID_COLS * cw + shear_w
    gap = (W - 4 * char_w) / 3                 # 两端对齐英文全宽

    hi = Image.new("RGBA", (W * SS, CN_H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(hi)
    for i, ch in enumerate("内容工厂"):
        cells = char_cells(ch)
        x0 = i * (char_w + gap)
        for gy in range(GRID_ROWS):
            dx = SKEW * (GRID_ROWS - 1 - gy) * cw      # 顶行右偏最大 → 右倾
            for gx in range(GRID_COLS):
                if not cells[gy][gx]:
                    continue
                t = (x0 + dx + (gx + 0.5) * cw) / W    # 全宽映射完整渐变
                px0 = (x0 + dx + gx * cw) * SS
                py0 = gy * cell * SS
                d.rectangle([px0, py0, px0 + cw * SS, py0 + cell * SS],
                            fill=lerp(t) + (255,))
    cn = hi.resize((W, CN_H), Image.LANCZOS)
    out.paste(cn, (0, EN_H + GAP1 + RULE_H + GAP2), cn)
    out.save(SRC)
    print("patched", SRC, out.size)


if __name__ == "__main__":
    main()
