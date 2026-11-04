"""Video creative analysis tools: review videos, tag category + hook, compare performance.

Workflow (also given to Claude in the server instructions):
1. list_video_ads: video ads with spend, hook rate, hold rate, CTR, CPC and tag status.
2. review_video_creative: hook frames, body frames and transcript of one video.
3. Propose a taxonomy (categories + hook types) from the top videos; once the user approves
   it, save it with set_creative_taxonomy(approved=True).
4. tag_video_creative for each video.
5. analyze_creative_performance: metrics grouped by category / hook type.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ads_mcp.common import check_date
from ads_mcp.meta.client import MetaClient, normalize_account_id
from ads_mcp.meta.creative_store import CreativeStore, data_dir
from ads_mcp.meta.video import HOOK_SECONDS, VideoToolsMissing, review_video, tool_status

AD_INSIGHT_FIELDS = (
    "ad_id,ad_name,adset_name,campaign_name,spend,impressions,reach,inline_link_clicks,actions,"
    "video_thruplay_watched_actions,video_p25_watched_actions,video_p50_watched_actions,"
    "video_p75_watched_actions,video_p100_watched_actions,video_avg_time_watched_actions"
)
CREATIVE_FIELDS = "creative{id,video_id,title,body,thumbnail_url,object_story_spec,asset_feed_spec}"
SUM_KEYS = ("spend", "impressions", "video_3s_views", "thruplays", "p25", "p50", "p75", "p100", "link_clicks")
GroupBy = Literal["category", "hook_type", "category_and_hook"]

_store: CreativeStore | None = None


def store() -> CreativeStore:
    global _store
    if _store is None:
        _store = CreativeStore()
    return _store


def _action_value(items: list[dict] | None, action_type: str | None = None) -> float:
    total = 0.0
    for item in items or []:
        if action_type is None or item.get("action_type") == action_type:
            total += float(item.get("value", 0))
    return total


def _ratio(num: float, den: float, digits: int = 4) -> float | None:
    return round(num / den, digits) if den else None


def video_id_of(creative: dict | None) -> tuple[str | None, int]:
    """Return (video_id, number of videos) for an ad creative."""
    if not creative:
        return None, 0
    if creative.get("video_id"):
        return str(creative["video_id"]), 1
    video_data = (creative.get("object_story_spec") or {}).get("video_data") or {}
    if video_data.get("video_id"):
        return str(video_data["video_id"]), 1
    videos = (creative.get("asset_feed_spec") or {}).get("videos") or []
    if videos and videos[0].get("video_id"):
        return str(videos[0]["video_id"]), len(videos)
    return None, 0


def ad_metrics(row: dict) -> dict:
    out = {
        "spend": float(row.get("spend", 0)),
        "impressions": float(row.get("impressions", 0)),
        "video_3s_views": _action_value(row.get("actions"), "video_view"),
        "thruplays": _action_value(row.get("video_thruplay_watched_actions")),
        "p25": _action_value(row.get("video_p25_watched_actions")),
        "p50": _action_value(row.get("video_p50_watched_actions")),
        "p75": _action_value(row.get("video_p75_watched_actions")),
        "p100": _action_value(row.get("video_p100_watched_actions")),
        "link_clicks": float(row.get("inline_link_clicks", 0)),
    }
    return out


def with_rates(m: dict) -> dict:
    """Add rates computed from summed counts (never by averaging per-ad rates)."""
    return {
        **{k: round(v, 2) for k, v in m.items() if k in SUM_KEYS},
        "hook_rate": _ratio(m["video_3s_views"], m["impressions"]),
        "hold_rate": _ratio(m["thruplays"], m["video_3s_views"]),
        "watch_25": _ratio(m["p25"], m["impressions"]),
        "watch_50": _ratio(m["p50"], m["impressions"]),
        "watch_75": _ratio(m["p75"], m["impressions"]),
        "watch_100": _ratio(m["p100"], m["impressions"]),
        "ctr": _ratio(m["link_clicks"], m["impressions"]),
        "cpc": _ratio(m["spend"], m["link_clicks"], 2),
    }


def _add(total: dict, m: dict) -> None:
    for k in SUM_KEYS:
        total[k] = total.get(k, 0.0) + m[k]


def _date_params(date_preset: str | None, since: str | None, until: str | None) -> dict:
    if since or until:
        if not (since and until):
            raise ToolError("Provide both since and until, or neither.")
        return {"time_range": {"since": check_date(since, "since"), "until": check_date(until, "until")}}
    return {"date_preset": date_preset or "last_30d"}


async def fetch_video_ads(client: MetaClient, account_id: str, dates: dict, max_ads: int) -> tuple[list[dict], int]:
    """Ad-level video metrics joined with each ad's video ID. Returns (video ads, non-video ad count)."""
    rows = await client.get_all(
        f"{normalize_account_id(account_id)}/insights",
        {"level": "ad", "fields": AD_INSIGHT_FIELDS, **dates},
        max_results=max_ads,
    )
    creatives: dict[str, dict] = {}
    ad_ids = [r["ad_id"] for r in rows]
    for i in range(0, len(ad_ids), 50):
        batch = await client.get("", {"ids": ",".join(ad_ids[i:i + 50]), "fields": CREATIVE_FIELDS})
        for ad_id, ad in batch.items():
            creatives[ad_id] = ad.get("creative") or {}

    ads, non_video = [], 0
    for row in rows:
        creative = creatives.get(row["ad_id"], {})
        video_id, n_videos = video_id_of(creative)
        if not video_id:
            non_video += 1
            continue
        ads.append({
            "ad_id": row["ad_id"],
            "ad_name": row.get("ad_name"),
            "campaign_name": row.get("campaign_name"),
            "adset_name": row.get("adset_name"),
            "video_id": video_id,
            "videos_in_ad": n_videos,
            "headline": creative.get("title"),
            "primary_text": creative.get("body"),
            "metrics": ad_metrics(row),
        })
    return ads, non_video


def register(mcp: MCPServer, client: Callable[[], MetaClient]) -> None:
    @mcp.tool()
    async def check_video_analysis_setup() -> dict:
        """Check that ffmpeg, whisper and the whisper model are installed for video review."""
        status = tool_status()
        status["ready"] = all(status.values())
        status["data_folder"] = str(data_dir())
        if not status["ready"]:
            status["fix"] = ("On the Mac run `brew install ffmpeg whisper-cpp`, then "
                             "`.venv/bin/python scripts/setup_video_analysis.py` in Concept-1, and restart Claude.")
        return status

    @mcp.tool()
    async def list_video_ads(
        account_id: str,
        date_preset: str | None = "last_30d",
        since: str | None = None,
        until: str | None = None,
        min_spend: float = 0,
        max_ads: int = 500,
    ) -> dict:
        """List video ads with spend, hook rate, hold rate, CTR, CPC and whether the video is tagged.

        hook_rate = 3-second views / impressions. hold_rate = ThruPlays / 3-second views.
        ctr = link clicks / impressions. cpc = spend / link clicks.
        Several ads can share one video; tags are stored per video_id.
        """
        ads, non_video = await fetch_video_ads(client(), account_id, _date_params(date_preset, since, until), max_ads)
        tags = store().get_tags([a["video_id"] for a in ads])
        out = []
        for ad in ads:
            if ad["metrics"]["spend"] < min_spend:
                continue
            tag = tags.get(ad["video_id"])
            out.append({
                **{k: v for k, v in ad.items() if k != "metrics"},
                **with_rates(ad["metrics"]),
                "category": tag["category"] if tag else None,
                "hook_type": tag["hook_type"] if tag else None,
            })
        out.sort(key=lambda a: a["spend"], reverse=True)
        videos = {a["video_id"] for a in out}
        return {
            "video_ads": len(out),
            "unique_videos": len(videos),
            "untagged_videos": len(videos - set(tags)),
            "non_video_ads_skipped": non_video,
            "ads": out,
        }

    @mcp.tool()
    async def review_video_creative(video_id: str, include_transcript: bool = True) -> list[str | Image]:
        """Show one video so you can judge its category and hook.

        Returns a text summary with the timed transcript, then two images:
        1. HOOK sheet: 8 frames from the first 3 seconds (read left-to-right, top-to-bottom).
        2. BODY sheet: 8 frames spread across the rest of the video.
        Results are cached, so reviewing the same video again is instant.
        """
        folder = data_dir() / "videos" / video_id
        cached = store().get_video(video_id)
        hook_sheet, body_sheet = folder / "hook.jpg", folder / "body.jpg"
        if not (cached and hook_sheet.exists() and (cached["transcript"] is not None or not include_transcript)):
            info = await client().get(video_id, {"fields": "source,length,title,description"})
            if not info.get("source"):
                raise ToolError(
                    f"Meta did not return a download link for video {video_id}. The token needs access to "
                    "the Page or ad account that owns the video."
                )
            try:
                review = await review_video(info["source"], folder, include_transcript)
            except VideoToolsMissing as e:
                raise ToolError(str(e))
            except RuntimeError as e:
                raise ToolError(f"Could not process video {video_id}: {e}")
            store().save_video(video_id, info.get("title"), review.duration, review.transcript)
            cached = store().get_video(video_id)

        lines = [f"Video {video_id}: {cached.get('title') or '(untitled)'}, "
                 f"{cached['length_seconds']:.1f} seconds long."]
        transcript = cached["transcript"]
        if transcript is None:
            lines.append("Transcript: not requested.")
        elif not transcript:
            lines.append("Transcript: no speech detected (music-only or silent video).")
        else:
            lines.append(f"Transcript (lines starting before {HOOK_SECONDS:.0f}s are the spoken hook):")
            for seg in transcript:
                marker = "HOOK " if seg["start"] < HOOK_SECONDS else ""
                lines.append(f"  {marker}[{seg['start']:.1f}s-{seg['end']:.1f}s] {seg['text']}")
        tag = store().get_tags([video_id]).get(video_id)
        if tag:
            lines.append(f"Current tag: category={tag['category']}, hook_type={tag['hook_type']}, "
                         f"hook_text={tag['hook_text']!r}")
        lines.append("Image 1 = HOOK sheet (first 3 seconds). Image 2 = BODY sheet (rest of the video).")

        result: list[str | Image] = ["\n".join(lines), Image(path=hook_sheet)]
        if body_sheet.exists():
            result.append(Image(path=body_sheet))
        return result

    @mcp.tool()
    async def get_creative_taxonomy() -> dict:
        """The category and hook-type lists used for tagging, and whether the user approved them."""
        return store().get_taxonomy()

    @mcp.tool()
    async def set_creative_taxonomy(
        categories: list[dict[str, str]], hooks: list[dict[str, str]], approved: bool = False
    ) -> dict:
        """Save the tagging lists. Each item is {"name": ..., "description": ...}.

        Only set approved=True after the user has explicitly approved these exact lists. Changing the
        lists later can orphan existing tags; those are reported back so they can be re-tagged.
        """
        for label, items in (("categories", categories), ("hooks", hooks)):
            names = [i.get("name", "").strip() for i in items]
            if not names or not all(names):
                raise ToolError(f"Every item in {label} needs a non-empty name.")
            if len(set(names)) != len(names):
                raise ToolError(f"Duplicate names in {label}.")
        store().set_taxonomy(categories, hooks, approved)
        cats, hook_names = store().taxonomy_names("category"), store().taxonomy_names("hook")
        orphaned = [vid for vid, t in store().get_tags().items()
                    if t["category"] not in cats or t["hook_type"] not in hook_names]
        return {**store().get_taxonomy(), "tags_needing_retag": orphaned}

    @mcp.tool()
    async def tag_video_creative(
        video_id: str, category: str, hook_type: str, hook_text: str = "", notes: str = ""
    ) -> dict:
        """Save the category and hook type for a video (replaces any earlier tag).

        category and hook_type must come from the approved taxonomy. hook_text is what is said or
        shown in the first 3 seconds; notes can hold anything else worth remembering.
        """
        if not store().taxonomy_approved():
            raise ToolError("The taxonomy isn't approved yet. Propose one to the user, then call "
                            "set_creative_taxonomy(approved=True).")
        if category not in store().taxonomy_names("category"):
            raise ToolError(f"Unknown category {category!r}. Use one of: {sorted(store().taxonomy_names('category'))}")
        if hook_type not in store().taxonomy_names("hook"):
            raise ToolError(f"Unknown hook type {hook_type!r}. Use one of: {sorted(store().taxonomy_names('hook'))}")
        store().save_tag(video_id, category, hook_type, hook_text, notes)
        return {"saved": store().get_tags([video_id])[video_id]}

    @mcp.tool()
    async def list_creative_tags() -> list[dict]:
        """All saved video tags."""
        return sorted(store().get_tags().values(), key=lambda t: t["tagged_at"])

    @mcp.tool()
    async def analyze_creative_performance(
        account_id: str,
        group_by: GroupBy = "category",
        date_preset: str | None = "last_30d",
        since: str | None = None,
        until: str | None = None,
        min_impressions: int = 1000,
        top_n: int = 5,
    ) -> dict:
        """Compare video performance by category, hook type, or both.

        For each group: videos, ads, spend, impressions, hook rate, hold rate, watch-through
        (25/50/75/100% of impressions), CTR and CPC. Rates are computed from summed counts.
        Groups or videos with fewer than min_impressions are flagged low_data: don't draw
        conclusions from them. Untagged videos are reported as their own group.
        Also returns the top and bottom videos by hook rate and by CTR.
        """
        ads, _ = await fetch_video_ads(client(), account_id, _date_params(date_preset, since, until), 2000)
        tags = store().get_tags()

        per_video: dict[str, dict[str, Any]] = {}
        for ad in ads:
            v = per_video.setdefault(ad["video_id"], {"ads": 0, "ad_names": [], "totals": {}})
            v["ads"] += 1
            v["ad_names"].append(ad["ad_name"])
            _add(v["totals"], ad["metrics"])

        def key_for(video_id: str) -> str:
            tag = tags.get(video_id)
            if not tag:
                return "(untagged)"
            if group_by == "category":
                return tag["category"]
            if group_by == "hook_type":
                return tag["hook_type"]
            return f"{tag['category']} | {tag['hook_type']}"

        groups: dict[str, dict[str, Any]] = {}
        for video_id, v in per_video.items():
            g = groups.setdefault(key_for(video_id), {"videos": 0, "ads": 0, "totals": {}})
            g["videos"] += 1
            g["ads"] += v["ads"]
            _add(g["totals"], v["totals"])

        total_spend = sum(g["totals"]["spend"] for g in groups.values()) or 1
        group_rows = sorted(
            (
                {
                    "group": name,
                    "videos": g["videos"],
                    "ads": g["ads"],
                    "spend_share": round(g["totals"]["spend"] / total_spend, 4),
                    **with_rates(g["totals"]),
                    "low_data": g["totals"]["impressions"] < min_impressions,
                }
                for name, g in groups.items()
            ),
            key=lambda r: r["spend"],
            reverse=True,
        )

        video_rows = []
        for video_id, v in per_video.items():
            tag = tags.get(video_id) or {}
            video_rows.append({
                "video_id": video_id,
                "ad_names": v["ad_names"][:3],
                "category": tag.get("category"),
                "hook_type": tag.get("hook_type"),
                "hook_text": tag.get("hook_text"),
                **with_rates(v["totals"]),
                "low_data": v["totals"]["impressions"] < min_impressions,
            })
        enough = [r for r in video_rows if not r["low_data"]]

        def ranked(metric: str, best: bool) -> list[dict]:
            rows = [r for r in enough if r[metric] is not None]
            return sorted(rows, key=lambda r: r[metric], reverse=best)[:top_n]

        return {
            "group_by": group_by,
            "groups": group_rows,
            "videos_total": len(per_video),
            "videos_untagged": sum(1 for vid in per_video if vid not in tags),
            "top_by_hook_rate": ranked("hook_rate", True),
            "bottom_by_hook_rate": ranked("hook_rate", False),
            "top_by_ctr": ranked("ctr", True),
            "bottom_by_ctr": ranked("ctr", False),
        }
