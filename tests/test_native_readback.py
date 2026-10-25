"""Readback result validation; fixtures do not establish native coverage."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def readback(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("native_readback", scripts / "native_readback.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_page_native_readback(readback):
    readback.validate_one_page_readback({"pages": [{"page": 1}]}, {"pages": 1})


@pytest.mark.parametrize("pages", [None, 0, 1, [], [{"page": 1}, {"page": 2}], [{}], [{"page": 0}], [{"page": True}]])
def test_doc_info_wrong_or_missing_page_is_rejected(readback, pages):
    with pytest.raises(AssertionError, match="doc_info"):
        readback.validate_one_page_readback({"pages": pages}, {"pages": 1})


@pytest.mark.parametrize("count", [None, 0, 2, True, 1.0, "1", [], {}])
def test_pdf_info_wrong_or_missing_page_count_is_rejected(readback, count):
    with pytest.raises(AssertionError, match="pdf_info"):
        readback.validate_one_page_readback({"pages": [{"page": 1}]}, {"pages": count})


@pytest.mark.parametrize("result", [None, [], 1, "pages"])
def test_nonobject_readback_is_rejected(readback, result):
    with pytest.raises(AssertionError, match="doc_info"):
        readback.validate_one_page_readback(result, {"pages": 1})
    with pytest.raises(AssertionError, match="pdf_info"):
        readback.validate_one_page_readback({"pages": [{"page": 1}]}, result)
