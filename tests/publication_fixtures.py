"""Small-data publisher fixtures; real example UI is covered by production browser tests."""

from pathlib import Path
import shutil


def dashboard_fixture(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects").mkdir(exist_ok=True)
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "dashboard", root / "dashboard")
    (root / "dashboard/index.html").write_text(
        '<!doctype html><html><body><script src="/app.js" type="module"></script></body></html>\n',
        encoding="utf-8")
    (root / "project_profiles.json").write_text('{"schema_version":1,"projects":{}}\n', encoding="utf-8")
    return root
