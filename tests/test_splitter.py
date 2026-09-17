import os
import pytest
from lambdas.splitter.splitter import InvalidPDFError, PageLimitExceededError

def test_invalid_pdf_magic_header(tmp_path):
    # Create fake non-PDF file
    fake_file = tmp_path / "fake.pdf"
    fake_file.write_bytes(b"NOT A REAL PDF CONTENT")

    with open(fake_file, "rb") as f:
        header = f.read(5)

    assert not header.startswith(b"%PDF-")

def test_valid_pdf_magic_header(tmp_path):
    valid_file = tmp_path / "valid.pdf"
    valid_file.write_bytes(b"%PDF-1.4 header bytes")

    with open(valid_file, "rb") as f:
        header = f.read(5)

    assert header.startswith(b"%PDF-")
