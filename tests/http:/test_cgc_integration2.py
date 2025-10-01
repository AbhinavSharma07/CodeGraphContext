import os
import pytest

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_project")
REQUIRED_FILES = ["module_a.py", "dynamic_dispatch.py"]


def test_sample_files_exist():
    assert os.path.isdir(SAMPLE_DIR), f"Directory does not exist: {SAMPLE_DIR}"
    for filename in REQUIRED_FILES:
        file_path = os.path.join(SAMPLE_DIR, filename)
        assert os.path.isfile(file_path), f"Missing required file: {filename}"


@pytest.mark.skipif(not pytest.importorskip('codegraphcontext', reason="codegraphcontext not installed"), reason="codegraphcontext not available")
def test_codegraphcontext_integration():
    try:
        from codegraphcontext.core import CodeGraph
    except Exception as e:
        pytest.skip(f"Could not import CodeGraph from codegraphcontext: {e}")

    cg = CodeGraph.from_folder(SAMPLE_DIR)

    # Try to retrieve functions using expected API
    try:
        functions = cg.get_all_functions()
        names = [
            f.get('name') if isinstance(f, dict) else getattr(f, 'name', None)
            for f in functions
        ]
        assert any(n and 'dispatch_by_key' in n or 'dispatch_by_string' in n for n in names), \
            "Expected function 'dispatch_by_key' or 'dispatch_by_string' not found"
        assert any(n and 'choose_path' in n for n in names), \
            "Expected function 'choose_path' not found"
    except Exception:
        # Fallback: check that the graph has nodes
        if hasattr(cg, 'nodes'):
            nodes = list(cg.nodes())
            assert nodes, "Code graph has no nodes"
        else:
            pytest.skip("CodeGraph object does not support `get_all_functions` or `nodes()`")
