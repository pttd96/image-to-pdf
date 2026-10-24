from typing import List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class HeadingBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["heading"] = "heading"
    text: str = ""
    level: int = 1  # 1 for chapter/document title, 2 for section, 3 for subsection
    align: Literal["left", "center", "right"] = "left"


class ParagraphBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["paragraph"] = "paragraph"
    text: str = ""  # Supports inline markdown (**bold**, *italics*)


class ListBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["list"] = "list"
    ordered: bool = False
    items: List[str] = Field(default_factory=list)  # Supports inline markdown


class QuoteBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["quote"] = "quote"
    text: str = ""


class TableBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["table"] = "table"
    title: Optional[str] = None
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)


class KeyValueBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["key_value"] = "key_value"
    label: str = ""
    value: str = ""


class AlignedLine(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""
    align: Literal["left", "center", "right", "justify"] = "left"
    type: Literal["title", "heading", "body", "meta", "signature", "list_item", "table_row"] = "body"
    box_2d: List[int] = Field(default_factory=lambda: [0, 0, 0, 0])
    font_size_pt: Optional[float] = None
    is_bold: bool = False
    is_italic: bool = False
    is_paragraph_start: bool = False


class PageMargins(BaseModel):
    model_config = ConfigDict(extra="ignore")
    left: int = 80
    right: int = 920
    top: int = 60
    bottom: int = 940


class AlignedPageLayout(BaseModel):
    model_config = ConfigDict(extra="ignore")
    page_margins: Optional[PageMargins] = None
    lines: List[AlignedLine] = Field(default_factory=list)


DocumentBlock = Union[
    HeadingBlock,
    ParagraphBlock,
    ListBlock,
    QuoteBlock,
    TableBlock,
    KeyValueBlock,
]


class PageDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    blocks: List[DocumentBlock] = Field(default_factory=list)
