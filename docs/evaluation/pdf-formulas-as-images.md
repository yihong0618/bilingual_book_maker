# Why display formulas are cropped as pictures instead of decoded

## Abstract

docling finds a display equation on the page and marks its region, but it never reads it. Its Markdown then carries `<!-- formula-not-decoded -->` where the equation stood, and the mathematics is simply missing from the book. docling can decode formulas to LaTeX with a vision model (`do_formula_enrichment`), but that path was slow and unreliable in two separate tries. The route instead crops each formula region from the rendered page and puts the picture where the equation stood. On the owner's test pages this turned 7 placeholders into 7 pictures. Across 24 crops on arXiv papers, no display equation was missed, duplicated or left as a placeholder. The pictures cost no model, no network and no measurable time.

## Setup

- **The owner's case:** a scanned quantum-mechanics textbook (Griffiths), pages 50 and 51. Seven placeholders across two pages; every display equation was absent from the book.
- **How a crop is made.** Each formula item carries its page and a box. docling reports the box relative to the page's CropBox origin, at the rendered size. The page is rendered at 3x and cropped:
    - The padding is asymmetric: 12 pt left and right, 2 pt vertically, growing up to 12 pt until it stops 1 pt short of any other item in the same column.
    - Overlapping boxes merge into one crop.
    - A region covering more than 80% of the page is refused as a layout mistake.
- **How it is placed.** Placement is by identity, not by count. Each undecoded formula gets a marker (`bbm-formula-NNNN`) that the serializer writes where the equation stands, and each marker is swapped for its own picture. So the serializer's walk order, for example grid order inside a table, cannot misplace one.
- **Verification:**
    - End to end on the Griffiths pages, with real models and a real translation.
    - An Opus corpus check on eight arXiv papers (first two pages each), plus three two-page runs on equation-heavy pages (hep-th pages 3 and 4, Adam 3 and 4, Transformer 4 and 5), because only two of the eight have a display equation on pages 1 and 2.

## Results

- Griffiths pages 50 to 51: **7 images, 0 placeholders** in `source.md` and `book_bilingual.md`. The EPUB carries 8 `<img>` tags (7 formulas and the figure) and no leaked placeholder. Equation 2.37 and the full multi-line derivation read back complete, closing bracket included.
- Corpus check: across **24 crops, no missed display equation, no duplicate, no leftover placeholder or marker**.
- Found along the way and fixed:
    - A tall brace clipped at a fixed 2 pt pad; the neighbour-aware vertical pad fixed it.
    - EPUB export failed on 9 of 12 bundles because docling writes the title as `##`. Every extraction now opens with a level-1 heading.
- Found and left, as upstream behavior:
    - Three crops carry the top of the next line where docling's own box overlaps it.
    - Two pictures sit one paragraph off where docling's reading order puts them.

**On decoding to LaTeX:**

- The September 21 comparison that first rejected `do_formula_enrichment` reported hallucinated equations, prose swallowed into `$$` blocks and a large slowdown. **The owner does not trust that record** ("a not so capable codex agent"), so its figures are not repeated here.
- The later structure study (September 23) tried enrichment once more. The cell ran 27 minutes without output and was killed.

## Decision

Display formulas are kept as pictures by default. `--no-formula-images` restores the bare placeholders. `do_formula_enrichment` stays off. The run prints `Display formulas kept as images: N. …`, and says that the equations are not translated.

What would change it: a formula decoder that is measured to be faithful on hard equations at a tolerable cost. A picture would still be the fallback for a scan, where it is what the reader wants.

## Limits

- **Inline mathematics** inside a sentence is not a formula region and is not covered. On a scan it arrives as whatever OCR made of it, and the translator carries that through.
- docling finds no formula regions at all on a page rotated 90 or 270 degrees.
- The corpus check is 24 crops on arXiv papers and one textbook scan. Books with many pages of equations were not run end to end.
- The pictures are not searchable and carry no alt text; Pandoc would turn alt text into a caption.

Source: docs/260922-feat-PDF_FORMULA_IMAGES.md; docs/260923-eval-PDF_STRUCTURE_FAULTS_LUNA_REGION_ROLES.md for the killed enrichment cell; docs/260921-eval-DOCLING_VS_OPENDATALOADER.md is superseded and not trusted (repository, dated records)
