"""Inline Markdown spans that are not prose: code and maths.

One definition for the two readers that must agree on them: the reading
edition carries them through a translation unchanged
(`pipeline/reading_edition.py`), and the echo check leaves them out when it
counts letters (`translation_checks.py`).
"""

import re

CODE_SPAN = re.compile(r"(`+).*?\1")

# Display forms come first: `$$...$$` must not be eaten by the inline
# `$...$` rule. The inline rule is Pandoc's own `tex_math_dollars` shape --
# no space after the opening `$`, none before the closing one, and no digit
# after it -- so `$5 and $10` and `$5-$10` stay prose here exactly as they
# do in Pandoc. `(pattern, flags)` pairs.
MATH_SPANS = (
    (r"\$\$.+?\$\$", re.DOTALL),
    (r"\\\[.+?\\\]", re.DOTALL),
    (r"\\\(.+?\\\)", re.DOTALL),
    (r"(?<![\\$])\$(?![\s$])(?:[^$\n\\]|\\.)+?(?<![\s\\])\$(?!\d)", 0),
)
