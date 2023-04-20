#!/usr/bin/env python3
from setuptools import find_packages, setup


def get_required_packges():
    packages = []
    with open("requirements.txt") as filep:
        for line in filep:
            packages.append(line.rstrip())

    return packages


setup(
    name="bbook_maker",
    description="The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt files and books.",
    version="0.3.0",
    license="MIT",
    author="yihong0618",
    author_email="zouzou0208@gmail.com",
    packages=find_packages(),
    url="https://github.com/yihong0618/bilingual_book_maker",
    python_requires=">=3.7",
    install_requires=get_required_packges(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    entry_points={
        "console_scripts": ["bbook_maker = book_maker.cli:main"],
    },
)
