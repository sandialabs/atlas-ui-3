import pytest

from modules.file_storage.manager import FileManager


def test_file_manager_content_type_and_category():
    fm = FileManager(s3_client=None)  # will construct default

    assert fm.get_content_type("report.pdf") == "application/pdf"
    assert fm.get_content_type("diagram.png") == "image/png"
    assert fm.get_content_type("unknown.bin") == "application/octet-stream"

    assert fm.categorize_file_type("main.py") == "code"
    assert fm.categorize_file_type("photo.jpg") == "image"
    assert fm.categorize_file_type("data.csv") == "data"
    assert fm.categorize_file_type("notes.txt") == "document"

    assert fm.should_display_in_canvas("plot.png") is True
    assert fm.should_display_in_canvas("archive.zip") is False
