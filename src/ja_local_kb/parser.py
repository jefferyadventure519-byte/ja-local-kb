"""Structure-aware Markdown parsing with stable chunk identities."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import frontmatter

from .errors import IdentityConflictError
from .models import ChunkingConfig, ParsedChunk, SourceSpec
from .registry import CLIENT_PROJECT_ID_PREFIX

H2_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
H3_RE = re.compile(r"(?m)^###\s+(.+?)\s*$")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_by_heading(
    text: str,
    pattern: re.Pattern[str],
) -> list[tuple[str, str]]:
    matches = list(pattern.finditer(text))
    if not matches:
        return [("正文", text.strip())] if text.strip() else []
    sections: list[tuple[str, str]] = []
    intro = text[: matches[0].start()].strip()
    if intro:
        sections.append(("文档导言", intro))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            (
                match.group(1).strip(),
                text[match.end() : end].strip(),
            )
        )
    return sections


def hard_split(value: str, max_chars: int, overlap: int) -> list[str]:
    if len(value) <= max_chars:
        return [value]
    parts: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + max_chars)
        part = value[start:end].strip()
        if part:
            parts.append(part)
        if end == len(value):
            break
        start = max(0, end - overlap)
    return parts


def pack_blocks(
    blocks: Iterable[str],
    max_chars: int,
    overlap: int,
) -> list[str]:
    packed: list[str] = []
    current = ""
    for raw_block in blocks:
        block = raw_block.strip()
        if not block:
            continue
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            packed.append(current)
        current = ""
        if len(block) <= max_chars:
            current = block
        else:
            packed.extend(hard_split(block, max_chars, overlap))
    if current:
        packed.append(current)
    return packed


def subdivide_section(
    heading: str,
    body: str,
    *,
    max_chars: int,
    overlap: int,
) -> list[tuple[str, str]]:
    combined = f"## {heading}\n\n{body}".strip()
    if len(combined) <= max_chars:
        return [(heading, combined)]
    h3_sections = split_by_heading(body, H3_RE)
    if any(title != "正文" for title, _ in h3_sections):
        results: list[tuple[str, str]] = []
        for subheading, subbody in h3_sections:
            subtext = f"## {heading}\n\n### {subheading}\n\n{subbody}".strip()
            for index, part in enumerate(
                pack_blocks(
                    re.split(r"\n\s*\n", subtext),
                    max_chars,
                    overlap,
                ),
                start=1,
            ):
                suffix = f" [{index}]" if len(subtext) > max_chars else ""
                results.append((f"{heading} > {subheading}{suffix}", part))
        return results
    return [
        (f"{heading} [{index}]", part)
        for index, part in enumerate(
            pack_blocks(
                re.split(r"\n\s*\n", combined),
                max_chars,
                overlap,
            ),
            start=1,
        )
    ]


def parse_source(
    path: Path,
    source: SourceSpec,
    config: ChunkingConfig,
) -> list[ParsedChunk]:
    raw_bytes = path.read_bytes()
    raw_text = raw_bytes.decode("utf-8-sig")
    post = frontmatter.loads(raw_text)
    metadata = dict(post.metadata)
    frontmatter_project_id = str(metadata.get("project_id", "")).strip()
    if frontmatter_project_id and frontmatter_project_id != source.project_id:
        raise IdentityConflictError(
            "Frontmatter project_id conflicts with the registered identity",
            details={
                "source_id": source.source_id,
                "registered_project_id": source.project_id,
                "frontmatter_project_id": frontmatter_project_id,
            },
        )
    frontmatter_client_id = str(metadata.get("client_id", "")).strip()
    if frontmatter_client_id and frontmatter_client_id != source.client_id:
        raise IdentityConflictError(
            "Frontmatter client_id conflicts with the registered identity",
            details={
                "source_id": source.source_id,
                "registered_client_id": source.client_id,
                "frontmatter_client_id": frontmatter_client_id,
            },
        )

    body = post.content.strip()
    document_hash = hashlib.sha256(raw_bytes).hexdigest()
    body_hash = sha256_text(body)
    metadata_json = json.dumps(metadata, ensure_ascii=False, default=str)
    occurrences: defaultdict[tuple[str, str], int] = defaultdict(int)
    chunks: list[ParsedChunk] = []

    for section_heading, section_body in split_by_heading(body, H2_RE):
        for heading, source_text in subdivide_section(
            section_heading,
            section_body,
            max_chars=config.max_chars,
            overlap=config.overlap_chars,
        ):
            content_hash = sha256_text(source_text)
            occurrence_key = (heading, content_hash)
            occurrence = occurrences[occurrence_key]
            occurrences[occurrence_key] += 1
            chunk_id = sha256_text(
                "\x1f".join(
                    [
                        source.source_id,
                        heading,
                        content_hash,
                        str(occurrence),
                    ]
                )
            )
            if source.project_id.startswith(CLIENT_PROJECT_ID_PREFIX):
                identity_lines = [
                    f"客户：{source.project_name}",
                    f"客户ID：{source.client_id}",
                ]
            else:
                identity_lines = [
                    f"项目：{source.project_name}",
                    f"项目ID：{source.project_id}",
                    f"客户ID：{source.client_id}",
                ]
            retrieval_text = "\n".join(
                [
                    *identity_lines,
                    f"文档角色：{source.document_role}",
                    f"文档类型：{metadata.get('doc_type', '')}",
                    f"章节：{heading}",
                    source_text,
                ]
            )
            chunks.append(
                ParsedChunk(
                    chunk_id=chunk_id,
                    source_id=source.source_id,
                    project_id=source.project_id,
                    project_name=source.project_name,
                    client_id=source.client_id,
                    document_role=source.document_role,
                    relative_path=source.relative_path,
                    absolute_path=str(path),
                    file_name=path.name,
                    doc_type=str(metadata.get("doc_type", "")),
                    document_status=str(metadata.get("status", "")),
                    heading=heading,
                    source_text=source_text,
                    retrieval_text=retrieval_text,
                    document_hash=document_hash,
                    body_hash=body_hash,
                    content_hash=content_hash,
                    embedding_hash=sha256_text(retrieval_text),
                    metadata_json=metadata_json,
                    wikilinks_json=json.dumps(
                        sorted(set(WIKILINK_RE.findall(source_text))),
                        ensure_ascii=False,
                    ),
                    updated=str(metadata.get("updated", "")),
                )
            )
    return chunks
