"""`make_book.py` with the suite's fixed stand-in translator as `faketest`.

For CI smokes that run the real command line with no translation service:
docker.yaml runs it inside each PDF image (tests/ mounted read-only, so the
image's own book_maker is what runs), and tests/test_ci_pdf_route_smoke.py
runs it on the host. The stand-in prefixes every translation with "译:".

    python tests/ci_fake_make_book.py --book_name two.pdf --to-epub \\
        --api_format faketest --language zh-hans --test --test_num 2
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The checkout (in the image, /app) first, so the book_maker beside tests/ is
# the one that runs and never an installed copy; then the suite's helpers.
sys.path[:0] = [str(HERE.parent), str(HERE)]

from pipeline_helpers import FakeTranslator  # noqa: E402

from book_maker import cli  # noqa: E402
from book_maker.translator import FORMAT_DICT  # noqa: E402

FORMAT = "faketest"


def main(argv=None):
    FORMAT_DICT[FORMAT] = FakeTranslator
    cli.FORMAT_DICT[FORMAT] = FakeTranslator
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
