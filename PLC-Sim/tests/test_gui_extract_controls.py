from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
HTML = (ROOT / "gui" / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "gui" / "static" / "project.js").read_text(encoding="utf-8")


def test_gvl_select_all_and_variable_filter_are_separate_controls() -> None:
    assert 'id="chkSelectAllGvls"' in HTML
    assert 'id="chkIncludeUnmarked" type="checkbox" checked' in HTML
    assert 'include_all: $("chkIncludeUnmarked").checked' in JS
    assert 'include_all: $("chkSelectAllGvls").checked' not in JS


def test_discovered_gvls_start_selected_and_keep_select_all_in_sync() -> None:
    assert 'class="gvlChk" value="${escapeHtml(fullPath)}" checked' in JS
    assert "bindGvlSelectionControls();" in JS
    assert '$("chkSelectAllGvls").onchange' in JS


def test_discovered_gvls_render_as_a_left_aligned_file_tree() -> None:
    assert 'list.classList.remove("empty-box")' in JS
    assert "renderGvlTree(r.gvls)" in JS
    assert 'class="gvl-folder-row"' in JS
    assert 'class="gvl-tree-item"' in JS


def test_editable_objects_render_as_a_left_aligned_file_tree() -> None:
    assert 'box.classList.remove("empty-box")' in JS
    assert 'class="object-folder-row"' in JS
    assert 'class="object-tree-children"' in JS
    assert 'class="object-item-text"' in JS
