"""人格本文の見出し構造を扱う。

Phase 0 の実測（docs/PHASE_0_FINDINGS.md §2）のとおり、4本文は平文ではなく
`【...】` 見出しで階層化された構造化文書である。ミラの本文には約60の見出しがある。
編集画面はこの構造を見せる必要がある。

原文を壊さないことが最優先。分割は位置で行い、正規化・strip・改行変換を一切しない。
`join(split(text)) == text` が常に成り立つ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 行頭の `【見出し】`。行の先頭にあるものだけを見出しとして扱う。
HEADING_PATTERN = re.compile(r"^【([^】\n]*)】[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class BodySection:
    """本文の一区画。`text` は見出し行を含む原文そのもの。"""

    heading: str
    text: str
    start: int
    end: int

    @property
    def is_preamble(self) -> bool:
        return self.heading == ""

    @property
    def body(self) -> str:
        """見出し行を除いた中身。先頭の改行は落とさない。"""
        if self.is_preamble:
            return self.text
        newline = self.text.find("\n")
        return self.text[newline + 1:] if newline >= 0 else ""

    @property
    def preview(self) -> str:
        stripped = self.body.strip()
        first = stripped.split("\n", 1)[0] if stripped else ""
        return first[:40]


def split_sections(text: str) -> tuple[BodySection, ...]:
    """本文を見出し単位へ分割する。見出しが無ければ全体を1区画として返す。"""
    if not text:
        return (BodySection(heading="", text="", start=0, end=0),)

    matches = list(HEADING_PATTERN.finditer(text))
    if not matches:
        return (BodySection(heading="", text=text, start=0, end=len(text)),)

    sections: list[BodySection] = []
    first_start = matches[0].start()
    if first_start > 0:
        sections.append(BodySection(heading="", text=text[:first_start], start=0, end=first_start))

    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            BodySection(heading=match.group(1), text=text[start:end], start=start, end=end)
        )
    return tuple(sections)


def join_sections(sections: tuple[BodySection, ...] | list[BodySection]) -> str:
    """分割の逆。原文を復元する。"""
    return "".join(section.text for section in sections)


def replace_section_text(text: str, index: int, new_text: str) -> str:
    """指定区画だけを差し替える。他の区画は1文字も変えない。"""
    sections = list(split_sections(text))
    if not 0 <= index < len(sections):
        raise IndexError(f"区画 {index} は範囲外です（全{len(sections)}区画）")
    current = sections[index]
    sections[index] = BodySection(
        heading=current.heading, text=new_text, start=current.start, end=current.start + len(new_text)
    )
    return join_sections(sections)


def heading_outline(text: str) -> tuple[str, ...]:
    return tuple(s.heading for s in split_sections(text) if not s.is_preamble)
