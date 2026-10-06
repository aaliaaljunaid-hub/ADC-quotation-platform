"""
Turns an uploaded reference document (supplier quote PDF, client brief .docx,
someone else's BOQ .xlsx, a .pptx concept deck, ...) into Markdown via
Microsoft's markitdown, so its text can be read, searched or pasted into a
project/quote without retyping it.

This is read-only and stateless: nothing is written to the catalog, project
or quote — unlike app/xlsx_importer.py, which understands one specific
workbook layout, markitdown knows nothing about our schema, so we only hand
the designer the extracted text and let them decide what to do with it.

Plugins and LLM image captioning are left off on purpose: conversion stays
local, deterministic and never sends the upload to a third party.
"""
import os
from io import BytesIO

from fastapi import HTTPException
from markitdown import (
    FileConversionException, MarkItDown, MissingDependencyException,
    StreamInfo, UnsupportedFormatException,
)

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # 20 MB — plenty for a quote/brief, bounds memory use
ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md",
    ".html", ".htm", ".json", ".xml",
}

_converter = MarkItDown(enable_plugins=False)


def convert_document_to_markdown(content: bytes, filename: str | None, content_type: str | None) -> dict:
    filename = filename or "document"
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise HTTPException(
            422,
            f"Unsupported document type '{extension or filename}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))}",
        )
    if not content:
        raise HTTPException(422, "Uploaded file is empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB limit")

    stream_info = StreamInfo(filename=filename, extension=extension, mimetype=content_type or None)
    try:
        result = _converter.convert_stream(BytesIO(content), stream_info=stream_info)
    except (UnsupportedFormatException, FileConversionException, MissingDependencyException) as exc:
        raise HTTPException(422, f"Could not convert '{filename}': {type(exc).__name__}") from exc

    markdown = result.markdown or ""
    return {
        "filename": filename,
        "title": result.title,
        "markdown": markdown,
        "character_count": len(markdown),
    }
