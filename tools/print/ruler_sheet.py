"""Generate a 1:1 A4 landscape sheet of rod scale strips for H-CV (2026 H).

Every strip is 250 mm long, 0 at the centre, ticks every 1 mm, labels -12..+12 cm.
Print at "Actual size / 100 %" and check the 100.0 mm calibration bar.

Usage:  python tools/print/ruler_sheet.py [output.pdf]
"""

import sys

from reportlab.lib.colors import Color, black, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
CJK = "STSong-Light"

PAGE_W, PAGE_H = landscape(A4)
LENGTH = 250.0          # mm, rod length
X0 = (PAGE_W / mm - LENGTH) / 2.0  # left end of every strip, mm

# id, title, strip height mm, line width mm, numbers?, style
VARIANTS = [
    ("A", "槽沿版 3.5mm宽 线0.3 无数字（推荐先试）", 3.5, 0.30, False, "ticks"),
    ("B", "槽沿版 3.5mm宽 线0.5 无数字", 3.5, 0.50, False, "ticks"),
    ("C", "槽沿版 3.5mm宽 线0.15 无数字（细线对照）", 3.5, 0.15, False, "ticks"),
    ("D", "5mm宽 线0.3 带数字", 5.0, 0.30, True, "ticks"),
    ("E", "7mm宽 线0.3 带数字（沿边+外侧）", 7.0, 0.30, True, "ticks"),
    ("F", "7mm宽 线0.5 带数字", 7.0, 0.50, True, "ticks"),
    ("G", "10mm宽 线0.3 带数字（题图样式，数字在下）", 10.0, 0.30, True, "ticks"),
    ("H", "备用：7mm 灰阶色块+刻度（需负责人批准才用）", 7.0, 0.30, True, "bands"),
    ("A'", "A 的备份（剪坏了可用）", 3.5, 0.30, False, "ticks"),
    ("A''", "A 的备份", 3.5, 0.30, False, "ticks"),
    ("E'", "E 的备份", 7.0, 0.30, True, "ticks"),
]

GAP = 9.0  # mm between strips (room for label + cut lines)
LIGHT = Color(0.80, 0.80, 0.80)
BAND_LIGHT = Color(0.88, 0.88, 0.88)
BAND_DARK = Color(0.45, 0.45, 0.45)


def line(c, x1, y1, x2, y2, w, color=black):
    c.setStrokeColor(color)
    c.setLineWidth(w * mm)
    c.line(x1 * mm, y1 * mm, x2 * mm, y2 * mm)


def strip(c, y_top, h, lw, numbers, style):
    """Draw one strip whose top edge (groove side) is at y_top (mm from page bottom)."""
    y_bot = y_top - h
    # cut guides: light dashed lines 1 mm outside the strip
    c.setDash(2, 2)
    line(c, X0 - 4, y_top + 1.0, X0 + LENGTH + 4, y_top + 1.0, 0.1, LIGHT)
    line(c, X0 - 4, y_bot - 1.0, X0 + LENGTH + 4, y_bot - 1.0, 0.1, LIGHT)
    c.setDash()
    # band background: cell k covers [10k, 10k+10) mm; cell 0 black, others alternate light/dark
    def cell_fill(k):
        if k == 0:
            return black
        return BAND_DARK if k % 2 else BAND_LIGHT
    if style == "bands":
        for k in range(-13, 13):
            a, b = max(k * 10.0, -125.0), min(k * 10.0 + 10.0, 125.0)
            if a >= b:
                continue
            c.setFillColor(cell_fill(k))
            c.rect((X0 + 125 + a) * mm, y_bot * mm, (b - a) * mm, h * mm, stroke=0, fill=1)
    # outline (thin, light) so the strip edges are visible for cutting/aligning
    c.setStrokeColor(LIGHT)
    c.setLineWidth(0.1 * mm)
    c.rect(X0 * mm, y_bot * mm, LENGTH * mm, h * mm, stroke=1, fill=0)

    num_zone = 0.30 * h if numbers else 0.0
    tick_zone = h - num_zone
    for mm_pos in range(-125, 126):
        x = X0 + 125 + mm_pos
        frac = 1.0 if mm_pos % 10 == 0 else (0.60 if mm_pos % 5 == 0 else 0.40)
        col = black
        if style == "bands" and mm_pos % 10 != 0 and cell_fill(mm_pos // 10) is not BAND_LIGHT:
            col = white
        line(c, x, y_top, x, y_top - tick_zone * frac, lw, col)
    if numbers:
        size = max(1.2, num_zone * 0.85)  # mm
        c.setFont("Helvetica-Bold", size * 2.835)
        for cm in range(-12, 13):
            if style == "bands":
                x = X0 + 125 + cm * 10 + 5  # centre of the cell to the right of the tick
                if cm == 12:
                    continue
                c.setFillColor(black if cell_fill(cm) is BAND_LIGHT else white)
            else:
                x = X0 + 125 + cm * 10
                c.setFillColor(black)
            c.drawCentredString(x * mm, (y_bot + num_zone * 0.12) * mm, str(cm))


def main(out):
    c = canvas.Canvas(out, pagesize=landscape(A4))
    c.setTitle("H-CV rod scale strips v1")
    top = PAGE_H / mm - 8
    c.setFillColor(black)
    c.setFont(CJK, 11)
    c.drawString(X0 * mm, top * mm, "H题 摆杆刻度尺 打印版 v1（1:1）  H-CV rod scale strips v1")
    c.setFont(CJK, 8)
    c.drawString(X0 * mm, (top - 5) * mm,
                 "打印：选“实际大小/100%”，不要“适合页面”。打印后用直尺量下面的校验条，应为 100.0 mm（误差>0.5mm 请重新设置打印）。")
    c.drawString(X0 * mm, (top - 9.5) * mm,
                 "每条长 250mm，0 在正中；上边缘=靠近凹槽的一侧（刻度从这条边开始）。贴在槽外沿顶面，不要贴进槽内。沿灰色虚线剪下。")
    # calibration bar 100 mm
    yb = top - 15
    line(c, X0, yb, X0 + 100, yb, 0.3)
    for k in range(0, 11):
        line(c, X0 + k * 10, yb, X0 + k * 10, yb + (2.5 if k % 5 == 0 else 1.5), 0.2)
    c.setFont("Helvetica", 7)
    c.drawString((X0 + 102) * mm, (yb - 0.8) * mm, "CHECK: this bar must measure 100.0 mm")

    y = yb - 9
    for vid, title, h, lw, numbers, style in VARIANTS:
        c.setFillColor(black)
        c.setFont(CJK, 7.5)
        c.drawString(X0 * mm, (y + 2.2) * mm, "%s  %s   [h=%.1fmm, line=%.2fmm]" % (vid, title, h, lw))
        strip(c, y, h, lw, numbers, style)
        y -= h + GAP
    c.setFont(CJK, 7)
    c.setFillColor(black)
    c.drawString(X0 * mm, 6 * mm,
                 "说明：A–C 宽 3.5mm（= (20−13)/2 槽沿宽），长刻度占满条宽、半厘米 60%、毫米 40%，用于程序识别；"
                 "D–G 带数字供人读；H 为备用方案，未经负责人批准不要贴到比赛摆杆上。")
    c.showPage()
    c.save()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ruler_sheet_v1.pdf")
