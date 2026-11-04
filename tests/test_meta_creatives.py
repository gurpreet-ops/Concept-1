import json
import stat

import httpx
import pytest
from mcp import Client

from ads_mcp.meta import creative_tools, server, video
from ads_mcp.meta.client import MetaClient

INSIGHTS = [
    # Two ads share video v1; v2 is a second video; ad a4 is an image ad.
    {"ad_id": "a1", "ad_name": "UGC 1", "spend": "100", "impressions": "10000", "inline_link_clicks": "100",
     "actions": [{"action_type": "video_view", "value": "3000"}, {"action_type": "link_click", "value": "100"}],
     "video_thruplay_watched_actions": [{"action_type": "video_view", "value": "600"}]},
    {"ad_id": "a2", "ad_name": "UGC 1 copy", "spend": "100", "impressions": "10000", "inline_link_clicks": "300",
     "actions": [{"action_type": "video_view", "value": "1000"}],
     "video_thruplay_watched_actions": [{"action_type": "video_view", "value": "400"}]},
    {"ad_id": "a3", "ad_name": "Demo", "spend": "50", "impressions": "500", "inline_link_clicks": "5",
     "actions": [{"action_type": "video_view", "value": "100"}]},
    {"ad_id": "a4", "ad_name": "Static", "spend": "20", "impressions": "4000", "inline_link_clicks": "40"},
]
CREATIVES = {
    "a1": {"creative": {"id": "c1", "video_id": "v1", "title": "Headline"}},
    "a2": {"creative": {"id": "c2", "object_story_spec": {"video_data": {"video_id": "v1"}}}},
    "a3": {"creative": {"id": "c3", "asset_feed_spec": {"videos": [{"video_id": "v2"}, {"video_id": "v3"}]}}},
    "a4": {"creative": {"id": "c4", "image_url": "x"}},
}


def meta_handler(request: httpx.Request):
    path = request.url.path
    if path.endswith("/insights"):
        return httpx.Response(200, json={"data": INSIGHTS})
    if "ids" in request.url.params:
        ids = request.url.params["ids"].split(",")
        return httpx.Response(200, json={i: CREATIVES[i] for i in ids})
    if path.endswith("/v1"):
        return httpx.Response(200, json={"source": "https://video.example/v1.mp4", "title": "UGC video"})
    return httpx.Response(404, json={"error": {"message": f"unexpected {path}"}})


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CREATIVE_DATA_DIR", str(tmp_path / "data"))
    creative_tools._store = None
    server._client = MetaClient("tok", transport=httpx.MockTransport(meta_handler))
    yield
    creative_tools._store = None


async def call(name, args=None):
    async with Client(server.mcp) as c:
        return await c.call_tool(name, args or {})


def payload(res):
    return json.loads(res.content[0].text)


async def approve_taxonomy():
    res = await call("set_creative_taxonomy", {
        "categories": [{"name": "UGC testimonial", "description": "Real customer talking"},
                       {"name": "Product demo", "description": "Shows the app in use"}],
        "hooks": [{"name": "Question", "description": "Opens with a question"},
                  {"name": "Bold claim", "description": "Opens with a strong statement"}],
        "approved": True,
    })
    assert not res.is_error, res


async def test_list_video_ads_joins_video_ids_and_rates():
    res = await call("list_video_ads", {"account_id": "123"})
    assert not res.is_error, res
    data = payload(res)
    assert data["video_ads"] == 3
    assert data["unique_videos"] == 2
    assert data["non_video_ads_skipped"] == 1
    a1 = next(a for a in data["ads"] if a["ad_id"] == "a1")
    assert a1["video_id"] == "v1"
    assert a1["hook_rate"] == 0.3
    assert a1["hold_rate"] == 0.2
    assert a1["ctr"] == 0.01
    assert a1["cpc"] == 1.0
    a3 = next(a for a in data["ads"] if a["ad_id"] == "a3")
    assert (a3["video_id"], a3["videos_in_ad"]) == ("v2", 2)


async def test_tagging_requires_approved_taxonomy_and_known_names():
    res = await call("tag_video_creative", {"video_id": "v1", "category": "UGC testimonial", "hook_type": "Question"})
    assert res.is_error and "isn't approved" in res.content[0].text
    await approve_taxonomy()
    res = await call("tag_video_creative", {"video_id": "v1", "category": "Meme", "hook_type": "Question"})
    assert res.is_error and "Unknown category" in res.content[0].text
    res = await call("tag_video_creative", {"video_id": "v1", "category": "UGC testimonial",
                                            "hook_type": "Question", "hook_text": "Tired of high fees?"})
    assert not res.is_error


async def test_taxonomy_change_reports_orphaned_tags():
    await approve_taxonomy()
    await call("tag_video_creative", {"video_id": "v1", "category": "UGC testimonial", "hook_type": "Question"})
    res = await call("set_creative_taxonomy", {
        "categories": [{"name": "Product demo"}], "hooks": [{"name": "Question"}], "approved": True})
    assert payload(res)["tags_needing_retag"] == ["v1"]


async def test_performance_groups_sum_counts_before_dividing():
    await approve_taxonomy()
    await call("tag_video_creative", {"video_id": "v1", "category": "UGC testimonial", "hook_type": "Question"})
    res = await call("analyze_creative_performance", {"account_id": "123", "group_by": "category"})
    assert not res.is_error, res
    data = payload(res)
    groups = {g["group"]: g for g in data["groups"]}
    ugc = groups["UGC testimonial"]
    # v1 = a1 + a2. Hold rate must be 1000 thruplays / 4000 3s views = 0.25; averaging the
    # per-ad hold rates (0.2 and 0.4) would wrongly give 0.3.
    assert ugc["hook_rate"] == 0.2
    assert ugc["hold_rate"] == 0.25
    assert ugc["ctr"] == 0.02
    assert (ugc["videos"], ugc["ads"], ugc["low_data"]) == (1, 2, False)
    untagged = groups["(untagged)"]
    assert untagged["low_data"] is True  # v2 has only 500 impressions
    assert data["videos_untagged"] == 1
    assert [v["video_id"] for v in data["top_by_hook_rate"]] == ["v1"]  # low-data v2 excluded from rankings


def _fake_tool(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


async def test_review_video_returns_transcript_and_sheets(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Fake ffprobe / ffmpeg / whisper-cli that produce the files the real tools would.
    _fake_tool(bin_dir / "ffprobe", 'echo \'{"format": {"duration": "21.5"}}\'\n')
    _fake_tool(bin_dir / "ffmpeg", 'for last; do :; done\necho fake > "$last"\n')
    transcript = {"transcription": [
        {"offsets": {"from": 0, "to": 2400}, "text": " Tired of paying high fees?"},
        {"offsets": {"from": 3200, "to": 6000}, "text": " Here's how I fixed it."},
    ]}
    (tmp_path / "transcript.json").write_text(json.dumps(transcript))
    _fake_tool(bin_dir / "whisper-cli",
               'while [ "$1" != "-of" ]; do shift; done\n'
               f'cp "{tmp_path / "transcript.json"}" "$2.json"\n')
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(video, "EXTRA_BIN_DIRS", [])
    model = video.whisper_model_path()
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    downloads = []

    async def fake_download(url, dest):
        downloads.append(url)
        dest.write_bytes(b"mp4")

    monkeypatch.setattr(video, "download", fake_download)

    res = await call("review_video_creative", {"video_id": "v1"})
    assert not res.is_error, res
    text = res.content[0].text
    assert "21.5 seconds" in text
    assert "HOOK [0.0s-2.4s] Tired of paying high fees?" in text
    assert "  [3.2s-6.0s] Here's how I fixed it." in text
    assert [c.type for c in res.content] == ["text", "image", "image"]
    assert downloads == ["https://video.example/v1.mp4"]

    await call("review_video_creative", {"video_id": "v1"})
    assert len(downloads) == 1  # second review served from cache


async def test_review_video_explains_missing_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(video, "EXTRA_BIN_DIRS", [])
    res = await call("review_video_creative", {"video_id": "v1"})
    assert res.is_error
    assert "brew install ffmpeg whisper-cpp" in res.content[0].text


def test_frame_times_cover_hook_and_body():
    hook, body = video.frame_times(20.0)
    assert hook[0] == 0 and max(hook) < 3 and len(hook) == 8
    assert len(body) == 8 and min(body) > 3 and max(body) < 20
    hook, body = video.frame_times(2.0)  # very short video: hook only
    assert max(hook) < 2 and body == []
