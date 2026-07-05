import sys
from pathlib import Path

from tqdm import tqdm

from book_maker.utils import prompt_config_to_kwargs

from .base_loader import BaseBookLoader

import fitz

from ebooklib import epub

PDF_LAYOUTS = ("top-bottom", "side-by-side")


def create_bilingual_pdf(pairs, out_path, title, layout="top-bottom"):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except Exception as e:
        print(f"pdf creation skipped: install reportlab first ({e})")
        return False

    if layout not in PDF_LAYOUTS:
        raise ValueError(f"unsupported pdf layout: {layout}")

    font = "Helvetica"
    ttf_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    try:
        if ttf_path.exists():
            font = "BBMArialUnicode"
            if font not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font, str(ttf_path)))
        else:
            font = "STSong-Light"
            if font not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(UnicodeCIDFont(font))
    except Exception:
        font = "Helvetica"

    page_w, page_h = A4
    left = right = 46
    top = 52
    bottom = 46
    font_size = 9.5
    label_size = 8
    leading = 13
    max_w = page_w - left - right
    col_gap = 18
    col_w = (max_w - col_gap) / 2
    width_cache = {}

    def width(text, size=font_size):
        key = (text, size)
        if key not in width_cache:
            width_cache[key] = pdfmetrics.stringWidth(text, font, size)
        return width_cache[key]

    def wrap_line(line, line_w):
        line = line.replace("\t", "    ").rstrip()
        if not line:
            return [""]
        out, cur, cur_w = [], [], 0.0
        for ch in line:
            ch_w = width(ch)
            if cur and cur_w + ch_w > line_w:
                out.append("".join(cur).rstrip())
                cur = [] if ch == " " else [ch]
                cur_w = 0.0 if ch == " " else ch_w
            else:
                cur.append(ch)
                cur_w += ch_w
        if cur:
            out.append("".join(cur).rstrip())
        return out or [""]

    def wrap_text(text, line_w):
        lines = []
        for raw in str(text or "").splitlines() or [""]:
            lines.extend(wrap_line(raw, line_w))
        return lines

    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle(title)
    page_no = 0

    def new_page():
        nonlocal page_no, y
        if page_no:
            c.showPage()
        page_no += 1
        c.setStrokeColor(colors.HexColor("#d9d9d9"))
        c.setLineWidth(0.4)
        c.line(left, page_h - 36, page_w - right, page_h - 36)
        c.line(left, 34, page_w - right, 34)
        c.setFillColor(colors.HexColor("#666666"))
        c.setFont(font, 8)
        header = title
        while width(header, 8) > max_w:
            header = header[:-4] + "..."
        c.drawString(left, page_h - 28, header)
        c.drawCentredString(page_w / 2, 22, str(page_no))
        c.setFillColor(colors.black)
        c.setFont(font, font_size)
        y = page_h - top

    def ensure(lines=1):
        if y - lines * leading < bottom:
            new_page()

    def draw_label(text, x):
        c.setFillColor(colors.HexColor("#666666"))
        c.setFont(font, label_size)
        c.drawString(x, y, text)
        c.setFillColor(colors.black)
        c.setFont(font, font_size)

    y = page_h - top
    new_page()

    for original, translated in pairs:
        if layout == "top-bottom":
            for label, text in (("Original", original), ("Translation", translated)):
                ensure(2)
                draw_label(label, left)
                y -= leading
                for line in wrap_text(text, max_w):
                    ensure()
                    if line:
                        c.drawString(left, y, line)
                    y -= leading
                y -= 4
            ensure()
            c.setStrokeColor(colors.HexColor("#eeeeee"))
            c.line(left, y + 4, page_w - right, y + 4)
            y -= 4
            continue

        original_lines = wrap_text(original, col_w)
        translated_lines = wrap_text(translated, col_w)
        row_count = max(len(original_lines), len(translated_lines))
        ensure(2)
        draw_label("Original", left)
        draw_label("Translation", left + col_w + col_gap)
        y -= leading
        for i in range(row_count):
            ensure()
            if i < len(original_lines) and original_lines[i]:
                c.drawString(left, y, original_lines[i])
            if i < len(translated_lines) and translated_lines[i]:
                c.drawString(left + col_w + col_gap, y, translated_lines[i])
            y -= leading
        ensure()
        c.setStrokeColor(colors.HexColor("#eeeeee"))
        c.line(left, y + 4, page_w - right, y + 4)
        y -= 6

    c.save()
    return True


class PDFBookLoader(BaseBookLoader):
    def __init__(
        self,
        pdf_name,
        model,
        key,
        resume,
        language,
        model_api_base=None,
        is_test=False,
        test_num=5,
        prompt_config=None,
        single_translate=False,
        context_flag=False,
        context_paragraph_limit=0,
        temperature=1.0,
        source_lang="auto",
        parallel_workers=1,
        pdf_layout="none",
    ) -> None:
        if fitz is None:
            raise Exception("PyMuPDF (fitz) is required to use PDF loader")

        self.pdf_name = pdf_name
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            temperature=temperature,
            source_lang=source_lang,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.p_to_save = []
        self.bilingual_result = []
        self.bilingual_temp_result = []
        self.test_num = test_num
        self.batch_size = 10
        self.single_translate = single_translate
        self.parallel_workers = max(1, parallel_workers)
        self.pdf_layout = pdf_layout

        try:
            doc = fitz.open(self.pdf_name)
            lines = []
            total_pages = len(doc)
            with tqdm(total=total_pages, desc="Extracting text", unit="pg") as pbar:
                for page in doc:
                    text = page.get_text("text")
                    if text:
                        lines.extend(text.splitlines())
                    pbar.update(1)
            self.origin_book = lines
        except Exception as e:
            raise Exception("can not load file") from e

        self.resume = resume
        self.bin_path = f"{Path(pdf_name).parent}/.{Path(pdf_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    def _make_new_book(self, book):
        pass

    def _try_create_epub(self):
        """Try to create an EPUB file from translated content.

        The EPUB is created from the `self.bilingual_result` list which alternates
        original and translated strings. If EPUB creation fails for any reason,
        this function will log the error and leave the TXT fallback intact.
        """
        if epub is None:
            # ebooklib not installed; skip EPUB generation
            return False

        if not self.bilingual_result:
            return False

        try:
            book = epub.EpubBook()
            title = Path(self.pdf_name).stem
            # Minimal metadata
            try:
                book.set_identifier(title)
                book.set_title(title)
                book.set_language(
                    self.translate_model.language
                    if hasattr(self.translate_model, "language")
                    else "en"
                )
            except Exception:
                # be tolerant about metadata API differences
                pass

            chapters = []
            # build chapters from bilingual_result (pairs)
            for i in range(0, len(self.bilingual_result), 2):
                orig = self.bilingual_result[i]
                trans = (
                    self.bilingual_result[i + 1]
                    if i + 1 < len(self.bilingual_result)
                    else ""
                )
                # basic html content: original then translated
                content = ""
                if orig:
                    content += (
                        '<div class="original">'
                        + "<p>"
                        + orig.replace("\n", "<br/>")
                        + "</p></div>"
                    )
                if trans:
                    content += (
                        '<div class="translation">'
                        + "<p>"
                        + trans.replace("\n", "<br/>")
                        + "</p></div>"
                    )

                chap = epub.EpubHtml(
                    title=f"part_{i//2}",
                    file_name=f"index_split_{i//2:03d}.xhtml",
                    lang=(
                        book.get_metadata("DC", "language")[0][0]
                        if book.get_metadata("DC", "language")
                        else None
                    ),
                )
                chap.content = content
                book.add_item(chap)
                chapters.append(chap)

            # table of contents and spine
            book.toc = tuple(chapters)
            book.spine = ["nav"] + chapters

            # add navigation files
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())

            out_path = f"{Path(self.pdf_name).parent}/{Path(self.pdf_name).stem}_bilingual.epub"
            epub.write_epub(out_path, book)
            return True
        except Exception as e:
            print(f"create epub failed: {e}")
            return False

    def _try_create_pdfs(self):
        if self.pdf_layout == "none":
            return

        layouts = PDF_LAYOUTS if self.pdf_layout == "all" else (self.pdf_layout,)
        if self.single_translate:
            pairs = [("", translated) for translated in self.bilingual_result]
        else:
            pairs = []
            for i in range(0, len(self.bilingual_result), 2):
                translated = (
                    self.bilingual_result[i + 1]
                    if i + 1 < len(self.bilingual_result)
                    else ""
                )
                pairs.append((self.bilingual_result[i], translated))

        for layout in layouts:
            out_path = (
                Path(self.pdf_name).parent
                / f"{Path(self.pdf_name).stem}_bilingual_{layout}.pdf"
            )
            if create_bilingual_pdf(pairs, out_path, Path(self.pdf_name).stem, layout):
                print(f"created pdf: {out_path.name}")

    def make_bilingual_book(self):
        index = 0
        p_to_save_len = len(self.p_to_save)

        try:
            sliced_list = [
                self.origin_book[i : i + self.batch_size]
                for i in range(0, len(self.origin_book), self.batch_size)
            ]
            with tqdm(total=len(sliced_list), desc="Translating", unit="b") as pbar:
                for i in sliced_list:
                    # fix the format thanks https://github.com/tudoujunha
                    batch_text = "\n".join(i)
                    if not batch_text.strip():
                        pbar.update(1)
                        continue
                    if not self.resume or index >= p_to_save_len:
                        try:
                            temp = self.translate_model.translate(batch_text)
                        except Exception as e:
                            print(e)
                            raise Exception("Something is wrong when translate") from e
                        self.p_to_save.append(temp)
                        if not self.single_translate:
                            self.bilingual_result.append(batch_text)
                        self.bilingual_result.append(temp)
                        self._save_progress()
                    index += self.batch_size
                    pbar.update(1)
                    if self.is_test and index > self.test_num:
                        break

            txt_out = (
                f"{Path(self.pdf_name).parent}/{Path(self.pdf_name).stem}_bilingual.txt"
            )
            self.save_file(txt_out, self.bilingual_result)

            # try to create an EPUB alongside the TXT fallback; if EPUB fails we still keep the TXT file
            epub_ok = self._try_create_epub()
            if epub_ok:
                print(f"created epub: {Path(self.pdf_name).stem}_bilingual.epub")
            else:
                print(
                    "epub creation skipped or failed; bilingual text saved to txt fallback"
                )
            self._try_create_pdfs()

        except (KeyboardInterrupt, Exception) as e:
            print(e)
            print("you can resume it next time")
            self._save_progress()
            self._save_temp_book()
            sys.exit(0)

    def _save_temp_book(self):
        index = 0
        sliced_list = [
            self.origin_book[i : i + self.batch_size]
            for i in range(0, len(self.origin_book), self.batch_size)
        ]

        for i in range(len(sliced_list)):
            batch_text = "".join(sliced_list[i])
            self.bilingual_temp_result.append(batch_text)
            if index < len(self.p_to_save):
                self.bilingual_temp_result.append(self.p_to_save[index])
            index += 1

        self.save_file(
            f"{Path(self.pdf_name).parent}/{Path(self.pdf_name).stem}_bilingual_temp.txt",
            self.bilingual_temp_result,
        )

    def _save_progress(self):
        try:
            with open(self.bin_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.p_to_save))
        except Exception as e:
            raise Exception("can not save resume file") from e

    def load_state(self):
        try:
            with open(self.bin_path, encoding="utf-8") as f:
                self.p_to_save = f.read().splitlines()
        except Exception as e:
            raise Exception("can not load resume file") from e

    def save_file(self, book_path, content):
        try:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
        except Exception as e:
            raise Exception("can not save file") from e
