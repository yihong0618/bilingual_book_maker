#!/usr/bin/env python3
from setuptools import find_packages, setup

packages = [
    "bs4",
    "openai>=1.1.1",
    "litellm",
    "requests",
    "ebooklib",
    "rich",
    "tqdm",
    "tiktoken",
    "PyDeepLX",
    "google-generativeai",
    "langdetect",
    "backoff",
]


setup(
    name="bbook_maker",
    description="The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt files and books.",
    version="0.7.9",
    license="MIT",
    author="yihong0618",
    author_email="zouzou0208@gmail.com",
    packages=find_packages(),
    url="https://github.com/yihong0618/bilingual_book_maker",
    python_requires=">=3.8",
    install_requires=packages,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    entry_points={
        "console_scripts": ["bbook_maker = book_maker.cli:main"],
    },
)
