"""Regenerate the four-page A4 OAK field target PDF. Uses workstation Pycairo."""
import argparse
import math
from pathlib import Path

import cairo

MM = 72 / 25.4
TARGETS = (
    ('Checkerboard', 9, 6, 20),
    ('Close-range checkerboard', 11, 8, 15),
    ('Symmetric circles', 7, 5, 20),
    ('Offset circles', 4, 7, 15),
)
DEFAULT = Path(__file__).resolve().parents[2] / 'hosts/pi5/demo/chennai/oak-targets-a4.pdf'


def label(ctx, x, y, text, size=3.4):
    ctx.select_font_face('DejaVu Sans')
    ctx.set_font_size(size)
    ctx.move_to(x, y)
    ctx.show_text(text)


def line(ctx, x1, y1, x2, y2):
    ctx.move_to(x1, y1)
    ctx.line_to(x2, y2)
    ctx.stroke()


def rulers(ctx):
    ctx.set_line_width(.23)
    line(ctx, 28, 190, 128, 190)
    for x in (28, 128):
        line(ctx, x, 188, x, 192)
    label(ctx, 28, 198, '100 mm between end ticks')
    line(ctx, 280, 65, 280, 165)
    for y in (65, 165):
        line(ctx, 278, y, 282, y)
    ctx.save()
    ctx.translate(287, 142)
    ctx.rotate(-math.pi / 2)
    label(ctx, 0, 0, '100 mm')
    ctx.restore()


def generate(output):
    surface = cairo.PDFSurface(str(output), 297 * MM, 210 * MM)
    ctx = cairo.Context(surface)
    for page, (name, columns, rows, pitch) in enumerate(TARGETS, 1):
        ctx.save()
        ctx.scale(MM, MM)
        checker = page <= 2
        kind = 'inner corners' if checker else 'circle centres'
        unit = 'square side' if checker else 'centre spacing' if page == 3 else 'row spacing'
        label(ctx, 12, 12, f'GARUDA OAK  /  {name}  /  Sheet {page} of 4', 4.6)
        label(ctx, 12, 19, f'{columns} x {rows} {kind}   |   Nominal {unit} {pitch} mm   |   A4 landscape, print at 100%')
        if checker:
            x = (297 - (columns + 1) * pitch) / 2
            y = (210 - (rows + 1) * pitch) / 2
            for r in range(rows + 1):
                for c in range(columns + 1):
                    if (r + c) % 2 == 0:
                        ctx.rectangle(x + c * pitch, y + r * pitch, pitch, pitch)
            ctx.fill()
        else:
            points = [((2*c + r%2 if page == 4 else c)*pitch, r*pitch)
                      for r in range(rows) for c in range(columns)]
            x = (297 - max(a for a, b in points)) / 2
            y = (210 - max(b for a, b in points)) / 2
            for a, b in points:
                ctx.arc(x + a, y + b, pitch * .23, 0, 2 * math.pi)
                ctx.fill()
        rulers(ctx)
        label(ctx, 150, 192, 'Measure both scales and the printed pitch.')
        ctx.restore()
        ctx.show_page()
    surface.finish()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, nargs='?', default=DEFAULT)
    generate(parser.parse_args().output)
