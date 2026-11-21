"""Generate the A4 P0-EXP03 reference grid. Requires reportlab."""

from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas


def draw(output):
    page = Canvas(str(output), pagesize=landscape(A4), invariant=1)
    page.setTitle("H-CV P0-EXP03 10 mm reference grid")
    center_x, center_y = 148.5 * mm, 105 * mm
    left, right = center_x - 140 * mm, center_x + 140 * mm
    bottom, top = center_y - 90 * mm, center_y + 90 * mm
    for offset in range(-140, 141, 10):
        page.setLineWidth(0.48 if offset % 50 == 0 else 0.28)
        page.setStrokeGray(0.18 if offset % 50 == 0 else 0.48)
        x = center_x + offset * mm
        page.line(x, bottom, x, top)
    for offset in range(-90, 91, 10):
        page.setLineWidth(0.48 if offset % 50 == 0 else 0.28)
        page.setStrokeGray(0.18 if offset % 50 == 0 else 0.48)
        y = center_y + offset * mm
        page.line(left, y, right, y)

    page.setStrokeGray(0)
    page.setLineWidth(1.55)
    page.line(center_x - 6 * mm, center_y, center_x + 6 * mm, center_y)
    page.line(center_x, center_y - 6 * mm, center_x, center_y + 6 * mm)
    page.setLineWidth(0.65)
    page.rect(left, bottom, right - left, top - bottom, fill=0)

    page.setFillGray(0)
    page.setFont("Helvetica-Bold", 11)
    page.drawString(left, 201 * mm, "H-CV / P0-EXP03")
    page.drawCentredString(center_x, 201 * mm, "10 mm reference grid")
    page.drawRightString(right, 201 * mm, "A4 / 100% scale")
    page.setFont("Helvetica", 8)
    page.drawString(left, 6 * mm, "Grid extent: 280 x 180 mm")
    page.drawRightString(right, 6 * mm, "Check with a ruler after printing")
    y = 8 * mm
    x1, x2 = center_x - 50 * mm, center_x + 50 * mm
    page.line(x1, y, x2, y)
    for x in (x1, x2):
        page.line(x, y - 2 * mm, x, y + 2 * mm)
    page.drawCentredString(center_x, 9.8 * mm, "100 mm")
    page.showPage()
    page.save()


if __name__ == "__main__":
    draw(Path(__file__).with_name("p0-exp03-grid-a4.pdf"))
