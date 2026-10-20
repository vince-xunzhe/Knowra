"""Only source-backed architecture figures become local report assets."""

import hashlib
import io
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

# ruff: noqa: F811 - pytest injects the imported shared fixture by name
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from models import RoadmapRun
from services import roadmap_figures as figures
from test_roadmap_fulltext import metadata, pdf_bytes
from test_roadmaps import env as api_env  # noqa: F401 - shared isolated database fixture


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(figures, "ASSET_DIR", tmp_path / "assets")
    return tmp_path


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (600, 200), "white").save(output, format="PNG")
    return output.getvalue()


def test_extracts_original_html_figure_with_paper_source_and_caption(assets, monkeypatch):
    calls = []
    html = b'<figure id="S3.F2"><img src="x2.png"><figcaption><span>Figure 2:</span> The network architecture.</figcaption></figure>'

    def fetch(url, aid, limit):
        calls.append(url)
        assert aid == "1706.03762"
        return (png_bytes() if url.endswith(".png") else html,
                url if url.endswith(".png") else url + "v5")

    monkeypatch.setattr(figures, "_fetch", fetch)
    result, warnings = figures.extract_figures(assets / "unused.pdf", metadata(), [
        {"page": 3, "text": "Figure 2: The network architecture."},
    ])
    assert not warnings and len(result) == 1
    image = result[0]
    assert image["kind"] == "figure" and image["page"] is None
    assert image["figureLabel"] == "Figure 2" and image["paperId"] == "classic"
    assert image["sourceUrl"] == "https://arxiv.org/html/1706.03762v5#S3.F2"
    assert calls[-1] == "https://arxiv.org/html/1706.03762v5/x2.png"
    raw = figures.asset_path(image["assetId"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == image["assetId"]
    assert Image.open(io.BytesIO(raw)).size == (600, 200)


def test_pdf_fallback_retains_actual_whole_page_and_does_not_claim_a_cropped_figure(assets, monkeypatch):
    def unavailable(*args):
        raise ValueError("HTML not available")

    monkeypatch.setattr(figures, "_html_figures", unavailable)
    path = assets / "original.pdf"
    path.write_bytes(pdf_bytes("Figure 1: The model architecture."))
    result, warnings = figures.extract_figures(path, metadata(), [
        {"page": 1, "text": "Figure 1: The model architecture."},
    ])
    assert len(result) == 1 and warnings
    image = result[0]
    assert image["kind"] == "page" and image["page"] == 1
    assert image["sourceUrl"] == "https://arxiv.org/pdf/1706.03762#page=1"
    picture = Image.open(figures.asset_path(image["assetId"]))
    assert picture.size == (600, 600)
    assert len(picture.getcolors(maxcolors=1000000)) > 1  # source text actually rendered


def test_no_caption_means_no_network_or_invented_image(assets, monkeypatch):
    def unexpected(*args):
        raise AssertionError("No figure fetch expected")

    monkeypatch.setattr(figures, "_html_figures", unexpected)
    result = figures.extract_figures(assets / "unused.pdf", metadata(), [
        {"page": 1, "text": "The architecture is described in Figure 1. Results follow."},
    ])
    assert result == ([], [])
    assert not figures.ASSET_DIR.exists()


@pytest.mark.parametrize("caption", [
    "Figure 1: Novel view synthesis results. We use a learning framework.",
    "Figure 5: Test accuracy of the network on ImageNet.",
    "Figure 6: Ablation results of our framework.",
    "Figure 7: Performance comparison of network architectures.",
])
def test_results_captions_are_not_misidentified_as_architecture(caption):
    assert not figures._architecture_caption(caption)
    assert not figures._page_candidates([{"page": 1, "text": caption}])


def test_architecture_caption_can_wrap_across_pdf_lines():
    result = figures._page_candidates([{"page": 4, "text":
        "Body text above.\nFigure 2: Overview of our\nnetwork architecture. Inputs are images.\n\nNext paragraph."}])
    assert len(result) == 1 and result[0]["page"] == 4
    assert "network architecture" in result[0]["caption"]
    assert "Next paragraph" not in result[0]["caption"]


def test_transparent_diagrams_keep_black_lines_on_white_background(assets):
    picture = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    ImageDraw.Draw(picture).rectangle((20, 20, 180, 80), outline="black", width=2)
    asset_id = figures._store_image(picture)
    with Image.open(figures.asset_path(asset_id)) as saved:
        assert saved.getpixel((0, 0)) == (255, 255, 255)
        assert saved.getpixel((20, 20)) == (0, 0, 0)


@pytest.mark.parametrize("url", [
    "https://example.com/html/1706.03762/x.png", "http://arxiv.org/html/1706.03762/x.png",
    "https://arxiv.org/html/1810.04805/x.png", "https://arxiv.org/html/1706.03762evil/x.png",
    "https://arxiv.org/html/1706.03762/../secret.png", "https://arxiv.org/html/1706.03762/%2e%2e/secret.png",
    "https://user@arxiv.org/html/1706.03762/x.png", "https://arxiv.org:444/html/1706.03762/x.png",
])
def test_refuses_image_urls_outside_exact_verified_paper(url):
    assert not figures._same_paper_url(url, "1706.03762")


def test_cancellation_during_html_fetch_is_not_swallowed(assets, monkeypatch):
    checks = []

    def cancelled():
        checks.append(True)
        if len(checks) == 2:
            raise RuntimeError("stop figure read")

    monkeypatch.setattr(figures, "_fetch", lambda url, *args: (b"<html></html>", url))
    with pytest.raises(RuntimeError, match="stop figure read"):
        figures.extract_figures(assets / "unused.pdf", metadata(), [{"page": 1, "text": "Figure 1: Network architecture."}], cancelled)


def test_rejects_oversized_images_and_asset_symlinks(assets):
    with pytest.raises(ValueError, match="dimensions"):
        figures._store_image(Image.new("RGB", (6000, 5000)))
    figures.ASSET_DIR.mkdir()
    image_id = "a" * 64
    figures.asset_path(image_id).symlink_to(assets / "outside.png")
    with pytest.raises(ValueError, match="symlink"):
        figures.asset_path(image_id)


def add_report(api_env, markdown, status="preview"):
    with api_env.db() as db:
        db.add(RoadmapRun(id="report-run", board_id="field", status=status,
                          snapshot={}, proposal={"report": markdown}))
        db.commit()


def test_only_retained_report_images_are_served_and_zip_is_self_contained(api_env, assets):
    asset_id = figures._store_image(Image.open(io.BytesIO(png_bytes())))
    image_url = figures.ASSET_PREFIX + asset_id + ".png"
    assert api_env.client.get(image_url).status_code == 404
    markdown = "# Review\n\n![Figure 1]('placeholder')\n".replace("'placeholder'", image_url)
    add_report(api_env, markdown)
    report = api_env.client.get("/api/roadmaps/report", params={"boardId": "field"}).json()
    assert report["hasAssets"] is True
    response = api_env.client.get(image_url)
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.content == figures.asset_path(asset_id).read_bytes()
    response = api_env.client.get("/api/roadmaps/report/export", params={"boardId": "field"})
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    with ZipFile(io.BytesIO(response.content)) as archive:
        name = "assets/" + asset_id + ".png"
        assert set(archive.namelist()) == {"report.md", name}
        assert "(" + name + ")" in archive.read("report.md").decode()
        assert archive.read(name) == figures.asset_path(asset_id).read_bytes()
    with api_env.db() as db:
        db.get(RoadmapRun, "report-run").status = "discarded"
        db.commit()
    assert api_env.client.get(image_url).status_code == 404


def test_missing_export_image_fails_instead_of_silently_creating_broken_markdown(api_env, assets):
    add_report(api_env, "![missing](" + figures.ASSET_PREFIX + "a" * 64 + ".png)")
    assert api_env.client.get("/api/roadmaps/report/export", params={"boardId": "field"}).status_code == 409
    assert api_env.client.get("/api/roadmaps/report/export", params={"boardId": "other"}).status_code == 404
