"""Unit tests that run in the add-on's own env, without vsnp_gui:

    env/bin/python tests/test_hooks.py

The contract check (bdtools addon-check __ADDON_ID__) runs every hook on
tests/contract/fixture through vsnp_gui itself; this file is for the add-on's
own logic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import addon_sdk as sdk  # noqa: E402


def main():
    assert sdk.CONTRACT == 1
    page = sdk.html_page("t", sdk.table_html(["a"], [[1]]))
    assert page.startswith("<!doctype html>") and "<td>1</td>" in page
    print("ok - add-on unit tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
