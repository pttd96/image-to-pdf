from typing import List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class HeadingBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["heading"] = "heading"
    text: str = ""
    level: int = 1  # 1 for document title, 2 for section/article, 3 for sub-item
    align: Literal["left", "center", "right"] = "left"


class ParagraphBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["paragraph"] = "paragraph"
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


DocumentBlock = Union[HeadingBlock, ParagraphBlock, TableBlock, KeyValueBlock]


class PageDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    blocks: List[DocumentBlock] = Field(default_factory=list)
