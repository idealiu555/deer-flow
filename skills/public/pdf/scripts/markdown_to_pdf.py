#!/usr/bin/env python3
"""Convert Markdown files to PDF using ReportLab.

This script is intentionally self-contained for skill usage in sandboxed runs.
It supports common Markdown blocks:
- headings
- paragraphs
- unordered/ordered lists (including nested lists)
- fenced code blocks
- blockquotes
- horizontal rules
"""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer


def _build_styles(base_font_size: int, body_font: str, heading_font: str) -> dict[str, ParagraphStyle]:
    sheet = getSampleStyleSheet()
    body = ParagraphStyle(
        "MdBody",
        parent=sheet["BodyText"],
        fontName=body_font,
        fontSize=base_font_size,
        leading=base_font_size + 4,
        spaceAfter=2,
    )
    h1 = ParagraphStyle("MdH1", parent=sheet["Heading1"], fontName=heading_font, fontSize=24, leading=28, spaceBefore=6, spaceAfter=8)
    h2 = ParagraphStyle("MdH2", parent=sheet["Heading2"], fontName=heading_font, fontSize=18, leading=22, spaceBefore=4, spaceAfter=6)
    h3 = ParagraphStyle("MdH3", parent=sheet["Heading3"], fontName=heading_font, fontSize=14, leading=18, spaceBefore=4, spaceAfter=4)
    list_style = ParagraphStyle("MdList", parent=body, leftIndent=0, spaceAfter=1)
    quote = ParagraphStyle(
        "MdQuote",
        parent=body,
        textColor=colors.HexColor("#444444"),
        backColor=colors.HexColor("#f7f7f7"),
        leftIndent=12,
        rightIndent=6,
        borderPadding=6,
    )
    code = ParagraphStyle(
        "MdCode",
        parent=sheet["Code"],
        fontName="Courier",
        fontSize=max(base_font_size - 1, 9),
        leading=max(base_font_size + 2, 12),
        backColor=colors.HexColor("#f5f5f5"),
        leftIndent=8,
        rightIndent=8,
        borderPadding=6,
    )
    return {"body": body, "h1": h1, "h2": h2, "h3": h3, "list": list_style, "quote": quote, "code": code}


def _select_text_fonts(markdown_text: str) -> tuple[str, str]:
    # Detect CJK content and use a built-in CID font to avoid tofu blocks.
    has_cjk = any("\u4e00" <= char <= "\u9fff" for char in markdown_text)
    if not has_cjk:
        return "Helvetica", "Helvetica-Bold"

    try:
        pdfmetrics.getFont("STSong-Light")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "STSong-Light"


def _render_inline(inline_token: Token) -> str:
    if not inline_token.children:
        return escape(inline_token.content)

    out: list[str] = []
    for child in inline_token.children:
        token_type = child.type
        if token_type == "text":
            out.append(escape(child.content))
        elif token_type == "softbreak" or token_type == "hardbreak":
            out.append("<br/>")
        elif token_type == "code_inline":
            out.append(f"<font face='Courier'>{escape(child.content)}</font>")
        elif token_type == "strong_open":
            out.append("<b>")
        elif token_type == "strong_close":
            out.append("</b>")
        elif token_type == "em_open":
            out.append("<i>")
        elif token_type == "em_close":
            out.append("</i>")
        elif token_type == "link_open":
            href = child.attrGet("href") or ""
            href = escape(href, quote=True)
            if href:
                out.append(f"<a href='{href}'>")
        elif token_type == "link_close":
            out.append("</a>")
        elif token_type == "image":
            alt = child.attrGet("alt") or child.content or "image"
            src = child.attrGet("src") or ""
            alt_text = escape(alt)
            src_text = escape(src)
            if src_text:
                out.append(f"[{alt_text}]({src_text})")
            else:
                out.append(f"[{alt_text}]")
        else:
            if child.content:
                out.append(escape(child.content))
    return "".join(out).strip()


def _parse_list(tokens: list[Token], start_idx: int, styles: dict[str, ParagraphStyle], ordered: bool) -> tuple[ListFlowable, int]:
    close_type = "ordered_list_close" if ordered else "bullet_list_close"
    cursor = start_idx + 1
    items: list[ListItem] = []

    while cursor < len(tokens) and tokens[cursor].type != close_type:
        if tokens[cursor].type != "list_item_open":
            cursor += 1
            continue

        cursor += 1
        item_content: list[object] = []
        while cursor < len(tokens) and tokens[cursor].type != "list_item_close":
            token = tokens[cursor]

            if token.type == "paragraph_open" and cursor + 1 < len(tokens) and tokens[cursor + 1].type == "inline":
                text = _render_inline(tokens[cursor + 1]) or " "
                item_content.append(Paragraph(text, styles["list"]))
                cursor += 3
                continue

            if token.type in {"bullet_list_open", "ordered_list_open"}:
                nested_ordered = token.type == "ordered_list_open"
                nested_list, cursor = _parse_list(tokens, cursor, styles, ordered=nested_ordered)
                item_content.append(Spacer(1, 2))
                item_content.append(nested_list)
                continue

            if token.type in {"fence", "code_block"}:
                item_content.append(Preformatted((token.content or "").rstrip("\n"), styles["code"]))
                cursor += 1
                continue

            cursor += 1

        if cursor < len(tokens) and tokens[cursor].type == "list_item_close":
            cursor += 1

        if not item_content:
            item_content = [Paragraph(" ", styles["list"])]

        items.append(ListItem(item_content))

    if cursor < len(tokens) and tokens[cursor].type == close_type:
        cursor += 1

    if ordered:
        start_raw = tokens[start_idx].attrGet("start")
        start_num = int(start_raw) if start_raw and start_raw.isdigit() else 1
        flowable = ListFlowable(items, bulletType="1", start=start_num, leftIndent=18)
    else:
        flowable = ListFlowable(items, bulletType="bullet", leftIndent=18)
    return flowable, cursor


def markdown_to_story(markdown_text: str, styles: dict[str, ParagraphStyle]) -> list[object]:
    parser = MarkdownIt("commonmark", {"html": False, "breaks": False})
    tokens = parser.parse(markdown_text)
    story: list[object] = []
    cursor = 0

    while cursor < len(tokens):
        token = tokens[cursor]

        if token.type == "heading_open":
            level = 1
            if token.tag.startswith("h") and len(token.tag) == 2 and token.tag[1].isdigit():
                level = int(token.tag[1])
            style_key = "h1" if level <= 1 else "h2" if level == 2 else "h3"
            if cursor + 1 < len(tokens) and tokens[cursor + 1].type == "inline":
                story.append(Paragraph(_render_inline(tokens[cursor + 1]) or " ", styles[style_key]))
            story.append(Spacer(1, 4))
            cursor += 3
            continue

        if token.type == "paragraph_open":
            if cursor + 1 < len(tokens) and tokens[cursor + 1].type == "inline":
                story.append(Paragraph(_render_inline(tokens[cursor + 1]) or " ", styles["body"]))
                story.append(Spacer(1, 4))
            cursor += 3
            continue

        if token.type in {"fence", "code_block"}:
            story.append(Preformatted((token.content or "").rstrip("\n"), styles["code"]))
            story.append(Spacer(1, 6))
            cursor += 1
            continue

        if token.type == "bullet_list_open":
            list_flowable, cursor = _parse_list(tokens, cursor, styles, ordered=False)
            story.append(list_flowable)
            story.append(Spacer(1, 5))
            continue

        if token.type == "ordered_list_open":
            list_flowable, cursor = _parse_list(tokens, cursor, styles, ordered=True)
            story.append(list_flowable)
            story.append(Spacer(1, 5))
            continue

        if token.type == "blockquote_open":
            cursor += 1
            while cursor < len(tokens) and tokens[cursor].type != "blockquote_close":
                if tokens[cursor].type == "paragraph_open" and cursor + 1 < len(tokens) and tokens[cursor + 1].type == "inline":
                    story.append(Paragraph(_render_inline(tokens[cursor + 1]) or " ", styles["quote"]))
                    story.append(Spacer(1, 3))
                    cursor += 3
                    continue
                cursor += 1
            if cursor < len(tokens) and tokens[cursor].type == "blockquote_close":
                cursor += 1
            story.append(Spacer(1, 4))
            continue

        if token.type == "hr":
            story.append(HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#9e9e9e")))
            story.append(Spacer(1, 6))
            cursor += 1
            continue

        cursor += 1

    if not story:
        story.append(Paragraph(" ", styles["body"]))
    return story


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a Markdown file into a PDF.")
    parser.add_argument("--input-file", required=True, help="Path to Markdown input file")
    parser.add_argument("--output-file", required=True, help="Path to output PDF file")
    parser.add_argument("--title", default="", help="Optional PDF title metadata")
    parser.add_argument("--page-size", choices=["a4", "letter"], default="a4", help="PDF page size")
    parser.add_argument("--font-size", type=int, default=11, help="Base body font size")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_file).expanduser().resolve()
    output_path = Path(args.output_file).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if input_path.suffix.lower() != ".md":
        raise ValueError(f"Input file must be a .md file: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    markdown_text = input_path.read_text(encoding="utf-8")
    page_size = A4 if args.page_size.lower() == "a4" else LETTER
    body_font, heading_font = _select_text_fonts(markdown_text)
    styles = _build_styles(max(args.font_size, 8), body_font=body_font, heading_font=heading_font)
    story = markdown_to_story(markdown_text, styles)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=page_size,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=args.title or input_path.stem,
        author="DeerFlow PDF Skill",
    )
    doc.build(story)
    print(f"PDF generated: {output_path}")


if __name__ == "__main__":
    main()
