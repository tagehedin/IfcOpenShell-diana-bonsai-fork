# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2026 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.
#
# This file was generated with the assistance of an AI coding tool.

"""BCF Project 2 report written straight to PDF - no browser and no PDF library, for when report.py
can't find Edge/Chrome (the Flatpak Blender on Linux can't start host programs at all). Same content
as the HTML report: overview table with links, then one page per topic. Text is the PDF built-in
Helvetica in WinAnsi encoding: Swedish and western European text is fine, other scripts print as '?'.
Snapshots go through Blender's own image loader and are stored as flat, deflated RGB."""

import datetime
import os
import tempfile
import unicodedata
import zlib
from pathlib import Path

import bpy
import numpy as np

from bonsai.bim.module.bcf2 import report

PAGE_W, PAGE_H = 595.28, 841.89  # A4
MARGIN = 34.0  # 12 mm, same as the HTML print styles
FOOTER = 16.0
WIDTH = PAGE_W - 2 * MARGIN
MAX_IMAGE_H = 425.0  # 150 mm
MAX_IMAGE_PX = 1600  # snapshots wider than this are scaled down; ~200 dpi at full page width

INK = (0.114, 0.137, 0.161)
MUTED = (0.373, 0.42, 0.463)
LINE = (0.835, 0.859, 0.882)
HEAD = (0.945, 0.957, 0.969)
STATUS_FILL = {
    "open": (0.973, 0.843, 0.843),
    "progress": (0.992, 0.906, 0.784),
    "done": (0.839, 0.937, 0.851),
    "": (0.89, 0.906, 0.922),
}

# Glyph widths (1/1000 em) for ASCII 32..126, from the Adobe core font metrics
_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 "
    "278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 "
    "611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833 "
    "556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)
_HELVETICA_BOLD = (
    "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 "
    "333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 722 667 "
    "611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 556 333 611 611 278 278 556 278 889 "
    "611 611 611 611 389 556 333 611 556 778 556 556 500 389 280 389 584"
)
WIDTHS = {
    "F1": [int(w) for w in _HELVETICA.split()],
    "F2": [int(w) for w in _HELVETICA_BOLD.split()],
    "F3": [int(w) for w in _HELVETICA.split()],  # Helvetica-Oblique shares Helvetica's widths
}
FONTS = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Helvetica-Oblique"}


def _clean(text) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\t", "    ")
    return "".join(c for c in text if c == "\n" or unicodedata.category(c)[0] != "C")


def _char_width(c: str, font: str) -> int:
    widths = WIDTHS[font]
    if 32 <= ord(c) <= 126:
        return widths[ord(c) - 32]
    base = unicodedata.normalize("NFD", c)[0]  # å -> a, Ö -> O
    return widths[ord(base) - 32] if 32 <= ord(base) <= 126 else 556


def text_width(text: str, font: str, size: float) -> float:
    return sum(_char_width(c, font) for c in text) * size / 1000


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    lines = []
    for paragraph in _clean(text).split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, font, size) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while text_width(word, font, size) > width:  # a word longer than the line: hard break
                cut = len(word) - 1
                while cut > 1 and text_width(word[:cut], font, size) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _pdf_string(text: str) -> str:
    raw = _clean(text).encode("cp1252", "replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _rgb(color) -> str:
    return " ".join(f"{c:.3f}" for c in color)


def load_snapshot(data: bytes) -> tuple[int, int, str, bytes] | None:
    """(width, height, PDF filter, stream) for a PNG/JPEG snapshot. JPEGs are embedded as they are unless
    oversized; everything else is decoded by Blender, flattened onto white and deflated."""
    is_png = data[:4] == b"\x89PNG"
    with tempfile.TemporaryDirectory(prefix="bcf_pdf_") as tmp:
        path = os.path.join(tmp, "snapshot.png" if is_png else "snapshot.jpg")
        Path(path).write_bytes(data)
        try:
            image = bpy.data.images.load(path, check_existing=False)
        except RuntimeError:
            return None
        try:
            w, h = image.size
            if not w or not h:
                return None
            if not is_png and w <= MAX_IMAGE_PX and image.channels == 3:
                return w, h, "/DCTDecode", data
            if w > MAX_IMAGE_PX:
                w, h = MAX_IMAGE_PX, max(1, round(h * MAX_IMAGE_PX / w))
                image.scale(w, h)
            pixels = np.empty(w * h * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
        finally:
            bpy.data.images.remove(image)
    pixels = pixels.reshape(h, w, 4)[::-1]  # Blender stores rows bottom-up
    alpha = pixels[..., 3:4]
    rgb = pixels[..., :3] * alpha + (1.0 - alpha)
    rgb = (np.clip(rgb, 0.0, 1.0) * 255 + 0.5).astype(np.uint8)
    return w, h, "/FlateDecode", zlib.compress(rgb.tobytes(), 6)


class Layout:
    """Pages as lists of PDF drawing operators, with a cursor that flows downwards."""

    def __init__(self):
        self.pages = []
        self.images = []  # (width, height, filter, stream)
        self.new_page()

    def new_page(self):
        self.page = {"ops": [], "links": []}
        self.pages.append(self.page)
        self.y = PAGE_H - MARGIN

    def room(self) -> float:
        return self.y - MARGIN - FOOTER

    def ensure(self, height: float) -> bool:
        """Starts a new page if `height` doesn't fit. True if it did."""
        if height > self.room() and self.y < PAGE_H - MARGIN:
            self.new_page()
            return True
        return False

    def text(self, x, baseline, text, font="F1", size=9.5, color=INK):
        self.page["ops"].append(
            f"{_rgb(color)} rg BT /{font} {size:g} Tf {x:.2f} {baseline:.2f} Td ({_pdf_string(text)}) Tj ET"
        )

    def fill(self, x, y, w, h, color):
        self.page["ops"].append(f"{_rgb(color)} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def stroke(self, x, y, w, h, color=LINE, width=0.6):
        self.page["ops"].append(f"{_rgb(color)} RG {width:g} w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re S")

    def paragraph(self, text, font="F1", size=9.5, color=INK, x=MARGIN, width=WIDTH, bar=None):
        """Wrapped text flowing over pages; `bar` draws a left rule in that color (comments)."""
        leading = size * 1.35
        for line in wrap(text, font, size, width):
            self.ensure(leading)
            if bar:
                self.fill(x - 8, self.y - leading, 2.2, leading, bar)
            self.text(x, self.y - size * 1.05, line, font, size, color)
            self.y -= leading

    def table(self, widths, rows, header=None, size=8.5):
        """Rows of cells (text, font, fill or None). The header repeats on every page. Returns each
        row's (page index, x, y, w, h) of its second cell, for links."""
        pad, leading = 3.5, size * 1.3
        placed = []

        def draw(cells):
            wrapped = [wrap(text, font, size, w - 2 * pad) for (text, font, _), w in zip(cells, widths)]
            max_lines = max(1, int((PAGE_H - 2 * MARGIN - FOOTER - 2 * pad - 40) / leading))
            wrapped = [lines[:max_lines] for lines in wrapped]
            height = max(len(lines) for lines in wrapped) * leading + 2 * pad
            if self.ensure(height) and header and cells is not header:
                draw(header)
            x = MARGIN
            for (text, font, fill), w, lines in zip(cells, widths, wrapped):
                if fill:
                    self.fill(x, self.y - height, w, height, fill)
                self.stroke(x, self.y - height, w, height)
                for i, line in enumerate(lines):
                    self.text(x + pad, self.y - pad - size * 0.95 - i * leading, line, font, size)
                x += w
            rect = (len(self.pages) - 1, MARGIN + widths[0], self.y - height, widths[1], height)
            self.y -= height
            return rect

        if header:
            draw(header)
        for cells in rows:
            placed.append(draw(cells))
        return placed

    def image(self, data: bytes) -> bool:
        snapshot = load_snapshot(data)
        if snapshot is None:
            return False
        w_px, h_px = snapshot[:2]
        scale = min(WIDTH / w_px, MAX_IMAGE_H / h_px)
        w, h = w_px * scale, h_px * scale
        self.ensure(h + 8)
        self.images.append(snapshot)
        x = MARGIN + (WIDTH - w) / 2
        self.page["ops"].append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {self.y - h:.2f} cm /Im{len(self.images)} Do Q")
        self.stroke(x, self.y - h, w, h)
        self.y -= h + 8
        return True


def build_pdf(props, bcfxml, bcf_file: str) -> tuple[bytes, int, int]:
    """Returns (pdf, topic count, image count)."""
    topics = report._ordered(list(props.topics), props.topic_columns)
    titles = {t.name: t.title for t in props.topics}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    title = props.name or Path(bcf_file).stem or "BCF report"
    labels = dict(report.FIELDS)
    lay = Layout()

    lay.paragraph(title, "F2", 18)
    lay.y -= 2
    meta = f"{len(topics)} topics · {os.path.basename(bcf_file) or 'unsaved BCF'} · exported {now}"
    lay.paragraph(meta, size=9, color=MUTED)
    lay.y -= 10
    widths = (22, 155, 60, 50, 55, 75, 55, 55.28)
    header = [(t, "F2", HEAD) for t in ["#", "Title"] + [labels[f] for f in report.OVERVIEW]]
    rows = []
    for n, topic in enumerate(topics, start=1):
        cells = [(str(n), "F1", None), (topic.title or "<no title>", "F1", None)]
        for field in report.OVERVIEW:
            value = report._value(field, getattr(topic, field))
            fill = STATUS_FILL[report.status_kind(value)] if field == "status" and value else None
            cells.append((value, "F1", fill))
        rows.append(cells)
    title_cells = lay.table(widths, rows, header)

    destinations = []
    images = 0
    for n, topic in enumerate(topics, start=1):
        lay.new_page()
        destinations.append(len(lay.pages) - 1)
        lay.paragraph(f"{n}. {topic.title or '<no title>'}", "F2", 14)
        lay.y -= 6
        snaps = report._snapshots(bcfxml, topic.name)
        shown = sum(lay.image(s) for s in snaps)
        images += shown
        if not shown:
            lay.paragraph("No viewpoint snapshot", "F3", 9.5, MUTED)
            lay.y -= 6
        rows = []
        for label, value in report.field_rows(topic, titles):
            fill = STATUS_FILL[report.status_kind(value)] if label == "Status" else None
            rows.append([(label, "F2", HEAD), (value, "F1", fill)])
        lay.table((95, WIDTH - 95), rows, size=9)
        if topic.description:
            lay.y -= 12
            lay.paragraph("Description", "F2", 10)
            lay.y -= 2
            lay.paragraph(topic.description)
        comments = sorted((c for c in topic.comments if c.comment), key=lambda c: c.date)
        if comments:
            lay.y -= 12
            lay.paragraph("Comments", "F2", 10)
            for c in comments:
                lay.y -= 5
                by = " · ".join(x for x in (c.author, report._value("creation_date", c.date)) if x)
                if by:
                    lay.paragraph(by, size=8, color=MUTED, x=MARGIN + 10, width=WIDTH - 10, bar=LINE)
                lay.paragraph(c.comment, x=MARGIN + 10, width=WIDTH - 10, bar=LINE)

    for (page, x, y, w, h), target in zip(title_cells, destinations):
        lay.pages[page]["links"].append(((x, y, x + w, y + h), target))
    total = len(lay.pages)
    for i, page in enumerate(lay.pages, start=1):
        footer = f"{title} · {i} / {total}"
        lay.page = page
        lay.text(PAGE_W - MARGIN - text_width(footer, "F1", 8), MARGIN - 4, footer, "F1", 8, MUTED)
    return write_pdf(lay, title), len(topics), images


def write_pdf(lay: Layout, title: str) -> bytes:
    objects: list[bytes | None] = []

    def reserve() -> int:
        objects.append(None)
        return len(objects)

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    def stream(dictionary: str, data: bytes) -> bytes:
        return f"<< {dictionary} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"

    catalog, pages_id, resources = reserve(), reserve(), reserve()
    page_ids = [reserve() for _ in lay.pages]
    fonts = " ".join(
        f"/{key} {add(f'<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>'.encode())} 0 R"
        for key, name in FONTS.items()
    )
    images = []
    for i, (w, h, filter_, data) in enumerate(lay.images, start=1):
        dictionary = f"/Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
        image_id = add(stream(dictionary + f"/BitsPerComponent 8 /Filter {filter_}", data))
        images.append(f"/Im{i} {image_id} 0 R")
    objects[resources - 1] = f"<< /Font << {fonts} >> /XObject << {' '.join(images)} >> >>".encode()

    for page, page_id in zip(lay.pages, page_ids):
        content = zlib.compress("\n".join(page["ops"]).encode("latin-1"))
        content_id = add(stream("/Filter /FlateDecode", content))
        annots = [
            add(
                f"<< /Type /Annot /Subtype /Link /Rect [{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f}] /Border [0 0 0] "
                f"/Dest [{page_ids[target]} 0 R /XYZ 0 {PAGE_H:.2f} null] >>".encode()
            )
            for (x1, y1, x2, y2), target in page["links"]
        ]
        objects[page_id - 1] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Resources {resources} 0 R "
            f"/Contents {content_id} 0 R /Annots [{' '.join(f'{a} 0 R' for a in annots)}] >>"
        ).encode()
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    info = add(f"<< /Title ({_pdf_string(title + ' - BCF report')}) /Producer (Bonsai BCF Project 2) >>".encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
