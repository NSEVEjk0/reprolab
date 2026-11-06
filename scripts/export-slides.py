"""Export the required four-slide PDF without downloads or browser services."""
from html.parser import HTMLParser
from pathlib import Path
import re
import textwrap

ROOT = Path(__file__).resolve().parent.parent


class Slides(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pages = []
        self.page = None
        self.block = None
        self.fragments = []
        self.depth = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "section":
            self.page = []
            self.pages.append(self.page)
        if self.page is None:
            return
        if tag == "br":
            self.fragments.append(" ")
        elif self.block is not None:
            self.depth += 1
        elif tag in ("h1", "h2", "p", "li") or tag == "div" and attrs.get("class") == "footer":
            self.block = (tag, attrs.get("class", ""))
            self.fragments = []
            self.depth = 0
        elif tag == "div" and not attrs:
            self.block = ("p", "step")
            self.fragments = []
            self.depth = 0

    def handle_data(self, data):
        if self.block:
            self.fragments.append(data)

    def handle_endtag(self, tag):
        if self.block:
            if self.depth:
                self.depth -= 1
            elif tag in ("h1", "h2", "p", "li", "div"):
                value = re.sub(r"\s+", " ", " ".join(self.fragments)).strip()
                self.page.append((*self.block, value))
                self.block = None
        if tag == "section":
            self.page = None


def literal(value):
    value = value.replace("→", " > ").replace("↗", "").replace("–", "-").replace("·", " | ")
    return value.encode("cp1252", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def main():
    source = Slides()
    source.feed((ROOT / "presentation/slides.html").read_text())
    if len(source.pages) != 4:
        raise ValueError("The required slide source must contain four complete pages.")
    objects = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    pages = []
    for blocks in source.pages:
        commands = [b"0.93 0.96 0.94 rg 0 0 960 540 re f", b"0.09 0.25 0.21 rg"]
        y = 491
        for tag, kind, value in blocks:
            heading = tag in ("h1", "h2")
            size = 35 if heading else 11 if kind in ("eyebrow", "footer") else 17
            width = 43 if heading else 124 if kind in ("eyebrow", "footer") else 100
            lines = textwrap.wrap(value, width=width)
            if kind == "footer":
                y = 27
            for line in lines:
                commands.append(b"BT /" + (b"F2" if heading else b"F1") + f" {size} Tf 48 {y} Td (".encode() + literal(line) + b") Tj ET")
                y -= size * 1.28
            if kind != "footer":
                y -= 14 if heading else 8
            if y < 42 and kind != "footer":
                raise ValueError("Slide text exceeds the page; shorten the source before export.")
        stream = b"\n".join(commands)
        page_id, stream_id = len(objects) + 1, len(objects) + 2
        pages.append(page_id)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {stream_id} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Count 4 /Kids [{' '.join(str(n)+' 0 R' for n in pages)}] >>".encode()
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    cross_reference = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for position in offsets[1:]:
        output.extend(f"{position:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{cross_reference}\n%%EOF\n".encode())
    destination = ROOT / "presentation/reprolab-slides.pdf"
    destination.write_bytes(output)
    print(f"Created {destination.name}: four slides, {len(output)} bytes.")


if __name__ == "__main__":
    main()
