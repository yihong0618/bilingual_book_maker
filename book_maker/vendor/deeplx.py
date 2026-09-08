"""DeepL's free web endpoint, vendored from PyDeepLX.

Source: https://github.com/OwO-Network/PyDeepLX (`PyDeepLX/PyDeepLX.py`),
by Vincent Young / OwO Network Limited. Copied here rather than depended on
because the package is archived — last release 2024-02, 202 stars — and the
`deepl-free` route calls exactly one of its functions. Ported to `requests`,
already a direct dependency of this project, so vendoring adds no import.

The request shape below is not ours to improve: it imitates DeepL's iOS app
down to the header set and the whitespace in `"method": "`, and the endpoint
rejects what does not look like that client. `tests/test_vendored_deeplx.py`
pins it for that reason.

    MIT License

    Copyright (c) 2023 OwO Network Limited

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the
    following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
    IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT
    OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
    THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

import json
import random
import time

import requests

DEEPL_API = "https://www2.deepl.com/jsonrpc"

# (connect, read), in seconds — httpx's own default, which is what this code
# waited before it was vendored onto `requests`.
TIMEOUT = (5, 5)

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "x-app-os-name": "iOS",
    "x-app-os-version": "16.3.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "x-app-device": "iPhone13,2",
    "User-Agent": "DeepL-iOS/2.9.1 iOS 16.3.0 (iPhone13,2)",
    "x-app-build": "510265",
    "x-app-version": "2.9.1",
    "Connection": "keep-alive",
}


class TooManyRequestsException(Exception):
    """A 429: this IP is blocked for a while. Known and common on this route."""

    def __str__(self):
        return (
            "DeepL free: too many requests, your IP has been blocked by DeepL "
            "temporarily; do not request it frequently in a short time."
        )


def get_i_count(text) -> int:
    return text.count("i")


def get_random_number() -> int:
    random.seed(time.time())
    return random.randint(8300000, 8399998) * 1000


def get_timestamp(i_count: int) -> int:
    ts = int(time.time() * 1000)
    if i_count == 0:
        return ts
    i_count += 1
    return ts - ts % i_count + i_count


def translate(
    text,
    sourceLang="auto",
    targetLang="en",
    numberAlternative=0,
    proxies=None,
):
    """One paragraph through DeepL's free endpoint.

    Kept faithful to the original, non-200 included: it prints the status and
    answers None rather than raising, which is upstream's behaviour and what
    the `deepl-free` route has always seen.
    """
    i_count = get_i_count(text)
    request_id = get_random_number()

    numberAlternative = max(min(3, numberAlternative), 0)

    post_data = {
        "jsonrpc": "2.0",
        "method": "LMT_handle_texts",
        "id": request_id,
        "params": {
            "texts": [{"text": text, "requestAlternatives": numberAlternative}],
            "splitting": "newlines",
            "lang": {
                "source_lang_user_selected": sourceLang,
                "target_lang": targetLang,
            },
            "timestamp": get_timestamp(i_count),
            "commonJobParams": {"wasSpoken": False, "transcribe_as": ""},
        },
    }
    body = json.dumps(post_data, ensure_ascii=False)

    # Not a typo and not cosmetic: the endpoint reads the spacing as a
    # fingerprint of its own client, and the rule is the id's.
    if (request_id + 5) % 29 == 0 or (request_id + 3) % 13 == 0:
        body = body.replace('"method":"', '"method" : "')
    else:
        body = body.replace('"method":"', '"method": "')

    response = requests.post(
        DEEPL_API,
        # Encoded here rather than handed over as text: the body may carry
        # non-ASCII, and utf-8 is what the header promises.
        data=body.encode("utf-8"),
        headers=HEADERS,
        proxies=proxies,
        # `requests` waits forever by default; `httpx`, which this code used
        # before it was vendored, waits five seconds. Same five here, connect
        # and read: an undocumented endpoint that stops answering must not
        # hang a book-length run on one paragraph.
        timeout=TIMEOUT,
    )

    if response.status_code == 429:
        raise TooManyRequestsException

    if response.status_code != 200:
        print("Error", response.status_code)
        return None

    result = response.json()["result"]["texts"][0]
    if numberAlternative <= 1:
        return result["text"]
    return [item["text"] for item in result["alternatives"]]
