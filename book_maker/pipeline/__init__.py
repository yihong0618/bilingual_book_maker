"""PDF/Markdown -> bilingual Markdown + EPUB bundle pipeline.

A thin staged harness around what the repository already has: extraction is
an adapter, translation is `MarkdownBookLoader` reached through the CLI, and
EPUB packaging is Pandoc. Nothing here parses Markdown for its own sake or
writes an EPUB by hand.
"""

from .errors import PipelineError

__all__ = ["PipelineError"]
