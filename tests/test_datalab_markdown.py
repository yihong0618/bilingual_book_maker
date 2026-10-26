"""Datalab's Markdown rewritten into what the pipeline carries.

Fixtures are shaped on a real Datalab conversion of a scanned textbook
(Purcell, pages 1-2 and 31-34, probe 261026): its page separators, the
`<math>` it writes inside table cells, and a paragraph the page break cut
with the page's footnotes printed between the halves.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import pandoc_or_skip  # noqa: E402

from book_maker.pipeline.datalab_markdown import is_datalab, normalize  # noqa: E402
from book_maker.pipeline.preflight import MARKDOWN_FORMAT  # noqa: E402

SEP = "-" * 48

CUT = f"""{{30}}{SEP}

One of the most stringent tests is provided by the magnetic field of the giant planet Jupiter, which was surveyed in the mission of Pioneer

<sup>†</sup>Joseph Priestly, "The History and Present State of Electricity," 1767.

<sup>‡</sup>E. R. Williams, *Phys. Rev. Lett.* **26**:721 (1971).

{{31}}{SEP}

10. The spatial variation of this field was carefully analyzed and found consistent.
"""

TABLE = f"""{{0}}{SEP}

# PHYSICAL CONSTANTS

|                    |       |                                           |            |
|--------------------|-------|-------------------------------------------|------------|
| <b>electron mass</b> | $m_e$ | <b><math>9.110 \\times 10^{{-28}}</math></b> | <b>gram</b> |
| <b>norm</b> | | <math>|a|</math> | |

$$W = \\int_{{r=\\infty}}^{{r_{{12}}}} \\frac{{q_1 q_2 (-dr)}}{{r^2}} = \\frac{{q_1 q_2}}{{r_{{12}}}} \\quad (3)$$
"""


def joined(text):
    return normalize(text).text


def test_separators_become_one_based_page_markers():
    result = normalize(CUT)
    assert "<!-- page 31 -->" in result.text
    assert "<!-- page 32 -->" in result.text
    assert SEP not in result.text
    assert result.pages == 2
    assert is_datalab(CUT) and not is_datalab(result.text)


def test_a_paragraph_cut_by_the_page_is_joined_and_the_footnotes_follow_it():
    text = joined(CUT)
    assert "mission of Pioneer 10. The spatial variation" in text
    # The footnotes and the marker come after the joined paragraph, in order.
    body = text.index("Pioneer 10.")
    first, second = text.index("<sup>†</sup>"), text.index("<sup>‡</sup>")
    assert body < first < second < text.index("<!-- page 32 -->")
    # Nothing left that Pandoc would read as a list starting at 10.
    assert "\n10. The" not in text


def test_the_rule_above_the_footnotes_moves_with_them():
    text = (
        f"{{402}}{SEP}\n\n(2) If molecules with permanent dipole moments are "
        f"present in the\n\n---\n\n<sup>†</sup>The prominence of D in "
        f"Maxwell's formulation.\n\n{{403}}{SEP}\n\nmedium, their "
        f"orientations will no longer be perfectly random.\n"
    )
    result = joined(text)
    assert "present in the medium, their orientations" in result
    assert result.index("orientations") < result.index("\n---\n")
    assert result.index("\n---\n") < result.index("<sup>†</sup>")


def test_a_rule_with_no_footnotes_under_it_ends_the_paragraph():
    no_join(
        "A long enough line of prose that simply has no punctuation at the\n\n---",
        "and a lower-case paragraph after a rule the book drew itself.",
    )


def test_two_numbered_items_across_the_break_stay_two_items():
    no_join(
        "1. The first instruction contains more than forty characters",
        "2. The second instruction is separate.",
    )
    no_join(
        "(a) The first instruction contains more than forty characters",
        "(b) the second instruction is separate.",
    )
    no_join(
        "1000. The first instruction contains more than forty characters",
        "1001. The second instruction is separate.",
    )


def test_a_hyphen_at_the_break_is_kept():
    text = (
        f"{{4}}{SEP}\n\nThus U is independent of the order in which the charges "
        f"were assem-\n\n{{5}}{SEP}\n\nbled. Since it is independent also of the "
        f"route.\n"
    )
    assert "were assem-bled. Since" in joined(text)


def no_join(head, tail, first=4, second=5):
    text = f"{{{first}}}{SEP}\n\n{head}\n\n{{{second}}}{SEP}\n\n{tail}\n"
    result = normalize(text)
    assert result.joined == 0, result.text
    return result.text


def test_a_finished_sentence_is_never_joined():
    no_join(
        "This sentence ends the page with a full stop at its very end.",
        "and this one starts in lower case for some reason of its own.",
    )


def test_an_equation_introduction_is_not_joined_to_its_equation():
    no_join(
        "The work required to effect this will be the integral below:",
        "$$W_3 = - \\int_{\\infty}^{P_3} \\mathbf{F}_3 \\cdot d\\mathbf{s}$$",
    )
    no_join(
        "The work required to bring the third charge in from far away will be",
        "$$W_3 = - \\int_{\\infty}^{P_3} \\mathbf{F}_3 \\cdot d\\mathbf{s}$$",
    )


def test_a_heading_or_a_capitalised_paragraph_starts_something_new():
    no_join(
        "A long enough line of prose that simply has no punctuation at the end",
        "# ENERGY OF A SYSTEM OF CHARGES",
    )
    no_join(
        "A long enough line of prose that simply has no punctuation at the end",
        "The next paragraph begins with a capital letter, as a new one does.",
    )


def test_a_short_figure_label_is_not_prose():
    no_join("12 such pairs", "and the rest of the caption in lower case here.")


def test_only_consecutive_pages_are_joined():
    # A page selection 5,9: what follows page 5 is not its continuation.
    no_join(
        "A long enough line of prose that simply has no punctuation at the end",
        "and a lower-case paragraph from a page four pages later in the book.",
        first=4,
        second=8,
    )


def test_math_elements_become_dollar_math_inline_in_a_table():
    text = joined(TABLE)
    assert "<math" not in text
    assert "$9.110 \\times 10^{-28}$" in text
    # A bar inside a cell would end the cell.
    assert "$\\vert{}a\\vert{}$" in text


def test_a_norm_in_a_table_cell_keeps_its_double_bars():
    text = joined(f"{{0}}{SEP}\n\n| a | b |\n|---|---|\n| <math>\\|v\\|</math> | 1 |\n")
    assert "$\\Vert{}v\\Vert{}$" in text


def test_a_block_math_element_outside_a_table_is_display_math():
    text = joined(f'{{0}}{SEP}\n\n<math display="block">E = mc^2 &lt; x</math>\n')
    assert "$$E = mc^2 < x$$" in text


def test_normalizing_twice_changes_nothing():
    for fixture in (CUT, TABLE):
        once = joined(fixture)
        assert joined(once) == once


def test_the_normalized_text_renders_as_mathml_in_pandoc():
    """What the EPUB writer is given: Pandoc's own reader and the HTML
    writer the EPUB uses, so table and display maths come out as MathML
    and nothing of Datalab's separators or <math> source is printed."""
    pandoc = pandoc_or_skip()
    text = joined(TABLE + "\n" + CUT)
    result = subprocess.run(
        [pandoc, "-f", MARKDOWN_FORMAT, "-t", "html5", "--mathml"],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    html = result.stdout
    assert "Could not convert TeX math" not in result.stderr
    assert html.count('<math display="inline"') >= 3
    assert '<math display="block"' in html
    assert "9.110" in html and "<table>" in html
    # Every formula is Pandoc's MathML (TeX only as its annotation), none
    # is Datalab's bare <math> element with TeX inside.
    assert "<math>" not in html
    assert html.count("<math ") == html.count(
        'xmlns="http://www.w3.org/1998/Math/MathML"'
    )
    assert SEP not in html and "{30}" not in html
    assert "<ol" not in html
