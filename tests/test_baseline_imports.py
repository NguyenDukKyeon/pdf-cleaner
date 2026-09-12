from pathlib import Path


def test_expected_desktop_entrypoints_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "desktop_app.py").is_file()
    assert (root / "native_api.py").is_file()
    assert (root / "backend" / "service.py").is_file()
    assert (root / "backend" / "engine" / "pdf_pipeline.py").is_file()
    assert (root / "frontend" / "static" / "app.js").is_file()
