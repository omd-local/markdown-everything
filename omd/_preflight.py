"""Serializable target inspection helpers for CLI preflight flows."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from omd import cli

DOUYIN_HOSTS = {"douyin.com", "v.douyin.com", "iesdouyin.com"}
INSTAGRAM_HOSTS = {"instagram.com"}
REEL_NEEDS_TOOLS = ["yt-dlp", "ffmpeg", "mlx_whisper"]
DOUYIN_NEEDS_TOOLS = ["f2", "ffmpeg", "mlx_whisper"]
PODCAST_NEEDS_TOOLS = ["ffmpeg", "mlx_whisper"]
XHS_NEEDS_TOOLS = ["tesseract", "ffmpeg", "mlx_whisper"]
WECHAT_NEEDS_TOOLS: list[str] = []
REDDIT_NEEDS_TOOLS: list[str] = []
X_NEEDS_TOOLS: list[str] = []
BSKY_NEEDS_TOOLS: list[str] = []
MASTODON_NEEDS_TOOLS: list[str] = []
THREADS_NEEDS_TOOLS: list[str] = []
HN_NEEDS_TOOLS: list[str] = []
TELEGRAM_NEEDS_TOOLS: list[str] = []
X_STATUS_PATH_RE = re.compile(r"/(?:i/(?:web/)?)?status(?:es)?/\d+(?:\b|/)?", re.IGNORECASE)
THREADS_POST_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MASTODON_STATUS_ID_RE = re.compile(r"^\d+$")
TELEGRAM_CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
NUMERIC_ID_RE = re.compile(r"^\d+$")


def inspect_target(target: str | Path) -> dict[str, object]:
    """Inspect a URL/share blob/file path using OMD's routing rules only."""
    raw = str(target)
    if cli.is_url(raw) or cli.URL_RE.search(raw):
        extracted = cli.extract_url_from_blob(raw)
        return inspect_url(extracted or raw, raw_input=raw)
    return inspect_path(Path(target))


def inspect_url(url: str, *, raw_input: str | None = None) -> dict[str, object]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    info = _base_result(
        input_value=raw_input or url,
        target_kind="url",
        backend="markitdown",
        detected_type="generic_url",
        extension=None,
        exists=None,
        needs_network=True,
        needs_cookies=False,
        needs_tools=["markitdown"],
        untrusted=True,
        warnings=[],
        risks=["remote_input", "network_fetch"],
        metadata={
            "url": url,
            "host": host,
            "scheme": parsed.scheme,
            "path": parsed.path,
        },
    )
    if cli.is_xhs_url(url):
        info.update(
            probable_backend="xhs",
            detected_type="xhs_url",
            needs_cookies=True,
            needs_tools=list(XHS_NEEDS_TOOLS),
        )
        info["metadata"].update(
            cookie_strategy="cookies_txt_required",
            cookie_domains=["xiaohongshu.com", "xhslink.com"],
            cookie_browser_supported=False,
        )
        _append_unique(info["risks"], "auth_required")
        _append_unique(
            info["warnings"],
            "Xiaohongshu note type is unknown until fetch; listed tools cover both image OCR and video transcription flows.",
        )
        _append_unique(
            info["warnings"],
            "Xiaohongshu/Rednote is an advanced local-only route: keep cookies on your own machine, hosted demo auth is disabled, and use may be constrained by platform terms or content rights.",
        )
        if host == "xhslink.com" or host.endswith(".xhslink.com"):
            _append_unique(info["risks"], "redirect_resolution")
            _append_unique(
                info["warnings"],
                "Shortlink input will be expanded before note inspection.",
            )
        return info
    if cli.is_podcast_url(url):
        info.update(
            probable_backend="podcast",
            detected_type="podcast_url",
            needs_tools=list(PODCAST_NEEDS_TOOLS),
        )
        _append_unique(info["risks"], "feed_lookup")
        return info
    if cli.is_wechat_url(url):
        info.update(
            probable_backend="wechat",
            detected_type="wechat_article_url" if cli.is_wechat_article_url(url) else "wechat_unsupported_url",
            needs_cookies=False,
            needs_tools=list(WECHAT_NEEDS_TOOLS),
        )
        if not cli.is_wechat_article_url(url):
            _mark_unsupported_post_shape(
                info,
                "WeChat conversion supports Official Account article URLs such as "
                "`https://mp.weixin.qq.com/s/...` or `https://mp.weixin.qq.com/s?...`; "
                "album, profile, history, and other /mp/... pages are not article bodies.",
            )
        _append_unique(
            info["warnings"],
            "WeChat article images are kept as original qpic links; offline image mirroring is not part of this route.",
        )
        return info
    if cli.is_reddit_url(url):
        info.update(
            probable_backend="reddit",
            detected_type="reddit_post_url",
            needs_cookies=False,
            needs_tools=list(REDDIT_NEEDS_TOOLS),
        )
        info["metadata"].update(
            cookie_strategy="public_only_no_cookie_passthrough",
            cookie_domains=["reddit.com", "redd.it"],
            cookie_browser_supported=False,
            comment_scope="op",
        )
        if not _valid_reddit_post_url(parsed):
            _mark_unsupported_post_shape(
                info,
                "Reddit conversion supports post URLs such as /r/<sub>/comments/<id>/..., "
                "/r/<sub>/s/<share-id>, or redd.it/<id>.",
            )
        _append_unique(
            info["warnings"],
            "Reddit conversion uses the public JSON endpoint and saves the OP only by default; "
            "choose OP + Top comments to include comment structure.",
        )
        _append_unique(
            info["warnings"],
            "Reddit conversion supports public posts only; private, deleted, quarantined, or login-gated posts may fail.",
        )
        return info
    if cli.is_x_url(url):
        info.update(
            probable_backend="xpost",
            detected_type="x_post_url",
            needs_cookies=False,
            needs_tools=list(X_NEEDS_TOOLS),
        )
        info["metadata"].update(
            cookie_strategy="public_only_no_cookie_passthrough",
            cookie_domains=["x.com", "twitter.com"],
            cookie_browser_supported=False,
        )
        if not _valid_x_post_url(parsed):
            _mark_unsupported_post_shape(info, "X/Twitter conversion supports single post URLs containing /status/<id>.")
        _append_unique(
            info["warnings"],
            "X/Twitter conversion uses public embed endpoints for single posts; private, deleted, age-restricted, or login-gated posts may fail.",
        )
        return info
    if cli.is_bsky_url(url):
        info.update(
            probable_backend="bsky",
            detected_type="bluesky_post_url",
            needs_cookies=False,
            needs_tools=list(BSKY_NEEDS_TOOLS),
        )
        if not _valid_bsky_post_url(parsed):
            _mark_unsupported_post_shape(info, "Bluesky conversion supports post URLs like /profile/<handle-or-did>/post/<id>.")
        _append_unique(
            info["warnings"],
            "Bluesky conversion uses the public AppView API and captures the post plus a bounded set of replies.",
        )
        return info
    if cli.is_mastodon_url(url):
        info.update(
            probable_backend="mastodon",
            detected_type="mastodon_status_url",
            needs_cookies=False,
            needs_tools=list(MASTODON_NEEDS_TOOLS),
        )
        if not _valid_mastodon_status_url(parsed):
            _mark_unsupported_post_shape(info, "Mastodon conversion supports public status URLs with a numeric status id.")
        _append_unique(
            info["warnings"],
            "Mastodon conversion uses the instance public statuses API for supported public instances.",
        )
        return info
    if cli.is_threads_url(url):
        info.update(
            probable_backend="threads",
            detected_type="threads_post_url",
            needs_cookies=False,
            needs_tools=list(THREADS_NEEDS_TOOLS),
        )
        info["metadata"].update(
            cookie_strategy="public_only_no_cookie_passthrough",
            cookie_domains=["threads.net", "threads.com"],
            cookie_browser_supported=False,
        )
        if not _valid_threads_post_url(parsed):
            _mark_unsupported_post_shape(info, "Threads conversion supports /@<handle>/post/<id> or /t/<id> URLs.")
        _append_unique(
            info["warnings"],
            "Threads conversion reads public page metadata and optional public oEmbed metadata; private, deleted, or login-gated posts may fail.",
        )
        return info
    if cli.is_hn_url(url):
        info.update(
            probable_backend="hn",
            detected_type="hacker_news_item_url",
            needs_cookies=False,
            needs_tools=list(HN_NEEDS_TOOLS),
        )
        if not _valid_hn_item_url(parsed):
            _mark_unsupported_post_shape(info, "Hacker News conversion supports item URLs like /item?id=<number>.")
        _append_unique(
            info["warnings"],
            "Hacker News conversion uses the official public Firebase item API and fetches a bounded comment tree.",
        )
        return info
    if cli.is_telegram_url(url):
        info.update(
            probable_backend="telegram",
            detected_type="telegram_post_url",
            needs_cookies=False,
            needs_tools=list(TELEGRAM_NEEDS_TOOLS),
        )
        if not _valid_telegram_post_url(parsed):
            _mark_unsupported_post_shape(info, "Telegram conversion supports public channel post URLs like /<channel>/<message-id>.")
        _append_unique(
            info["warnings"],
            "Telegram conversion uses the public t.me channel page and supports public channel posts only.",
        )
        return info
    if cli.is_reel_url(url):
        if _host_matches(host, DOUYIN_HOSTS):
            info.update(
                probable_backend="reel",
                detected_type="douyin_url",
                needs_cookies=True,
                needs_tools=list(DOUYIN_NEEDS_TOOLS),
            )
            info["metadata"].update(
                cookie_strategy="cookies_txt_required",
                cookie_domains=["douyin.com", "v.douyin.com", "iesdouyin.com"],
                cookie_browser_supported=False,
            )
            _append_unique(info["risks"], "auth_required")
            _append_unique(
                info["warnings"],
                "Douyin routes through the f2 downloader and requires a valid cookies.txt file.",
            )
            _append_unique(
                info["warnings"],
                "Douyin is an advanced local-only route: keep cookies on your own machine, hosted demo auth is disabled, and use may be constrained by platform terms or content rights.",
            )
            return info
        info.update(
            probable_backend="reel",
            detected_type="reel_url",
            needs_tools=list(REEL_NEEDS_TOOLS),
        )
        if _host_matches(host, INSTAGRAM_HOSTS):
            info["metadata"].update(
                cookie_strategy="browser_or_cookies_txt_optional",
                cookie_domains=["instagram.com"],
                cookie_browser_supported=True,
            )
            _append_unique(
                info["warnings"],
                "Instagram downloads may require cookies even though routing does not require them up front; private, deleted, age-restricted, or login-gated posts may fail.",
            )
            _append_unique(
                info["warnings"],
                "Instagram is an advanced access-sensitive route; hosted demo cookie workflows are disabled and use may be constrained by platform terms or content rights.",
            )
        return info
    return info


def inspect_path(path: str | Path) -> dict[str, object]:
    file_path = Path(path).expanduser()
    ext = file_path.suffix.lower() or None
    exists = file_path.exists()
    if not exists:
        return _base_result(
            input_value=str(path),
            target_kind="file",
            backend=None,
            detected_type="missing_file",
            extension=ext,
            exists=False,
            needs_network=False,
            needs_cookies=False,
            needs_tools=[],
            untrusted=True,
            warnings=[f"Path does not exist: {file_path}"],
            risks=["missing_input"],
            metadata={"path": str(file_path)},
        )
    if file_path.is_dir():
        supported = [
            child.name
            for child in sorted(file_path.iterdir())
            if child.is_file() and child.suffix.lower() in _supported_extensions()
        ]
        warnings: list[str] = []
        if not supported:
            warnings.append("Directory contains no files supported by current routing rules.")
        return _base_result(
            input_value=str(path),
            target_kind="directory",
            backend="directory",
            detected_type="directory",
            extension=None,
            exists=True,
            needs_network=False,
            needs_cookies=False,
            needs_tools=[],
            untrusted=True,
            warnings=warnings,
            risks=["batch_input"],
            metadata={"path": str(file_path), "supported_entries": supported},
        )
    if ext in cli.IMAGE_EXTS:
        return _base_result(
            input_value=str(path),
            target_kind="file",
            backend="tesseract",
            detected_type="image_file",
            extension=ext,
            exists=True,
            needs_network=False,
            needs_cookies=False,
            needs_tools=["tesseract"],
            untrusted=True,
            warnings=[],
            risks=["untrusted_file"],
            metadata={"path": str(file_path)},
        )
    if ext in cli.AUDIO_EXTS:
        return _base_result(
            input_value=str(path),
            target_kind="file",
            backend="reel",
            detected_type="audio_file",
            extension=ext,
            exists=True,
            needs_network=False,
            needs_cookies=False,
            needs_tools=list(PODCAST_NEEDS_TOOLS),
            untrusted=True,
            warnings=[],
            risks=["untrusted_file"],
            metadata={"path": str(file_path)},
        )
    if ext in cli.MARKITDOWN_EXTS:
        return _base_result(
            input_value=str(path),
            target_kind="file",
            backend="markitdown",
            detected_type="document_file",
            extension=ext,
            exists=True,
            needs_network=False,
            needs_cookies=False,
            needs_tools=["markitdown"],
            untrusted=True,
            warnings=[],
            risks=["untrusted_file", "parser_input"],
            metadata={"path": str(file_path)},
        )
    return _base_result(
        input_value=str(path),
        target_kind="file",
        backend=None,
        detected_type="unsupported_file",
        extension=ext,
        exists=True,
        needs_network=False,
        needs_cookies=False,
        needs_tools=[],
        untrusted=True,
        warnings=[f"Unsupported extension for current routing rules: {ext or '(none)'}"],
        risks=["unsupported_input"],
        metadata={"path": str(file_path)},
    )


def _base_result(
    *,
    input_value: str,
    target_kind: str,
    backend: str | None,
    detected_type: str,
    extension: str | None,
    exists: bool | None,
    needs_network: bool,
    needs_cookies: bool,
    needs_tools: list[str],
    untrusted: bool,
    warnings: list[str],
    risks: list[str],
    metadata: dict[str, object],
) -> dict[str, object]:
    return {
        "input": input_value,
        "target_kind": target_kind,
        "probable_backend": backend,
        "detected_type": detected_type,
        "extension": extension,
        "exists": exists,
        "needs_network": needs_network,
        "needs_cookies": needs_cookies,
        "needs_tools": list(needs_tools),
        "untrusted": untrusted,
        "warnings": list(warnings),
        "risks": list(risks),
        "metadata": metadata,
    }


def _append_unique(items: object, value: str) -> None:
    if isinstance(items, list) and value not in items:
        items.append(value)


def _mark_unsupported_post_shape(info: dict[str, object], warning: str) -> None:
    _append_unique(info["risks"], "unsupported_input")
    _append_unique(info["warnings"], warning)


def _host_matches(host: str, hosts: set[str]) -> bool:
    return any(host == candidate or host.endswith("." + candidate) for candidate in hosts)


def _supported_extensions() -> set[str]:
    return set(cli.IMAGE_EXTS) | set(cli.AUDIO_EXTS) | set(cli.MARKITDOWN_EXTS)


def _path_parts(parsed: ParseResult) -> list[str]:
    return [unquote(part) for part in parsed.path.split("/") if part]


def _valid_reddit_post_url(parsed: ParseResult) -> bool:
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if host == "redd.it" or host.endswith(".redd.it"):
        return bool(path.strip("/").split("/", 1)[0])
    parts = _path_parts(parsed)
    if len(parts) == 4 and parts[0].lower() == "r" and parts[2].lower() == "s":
        return bool(parts[1] and parts[3])
    return "/comments/" in path


def _valid_x_post_url(parsed: ParseResult) -> bool:
    return bool(X_STATUS_PATH_RE.search(parsed.path))


def _valid_bsky_post_url(parsed: ParseResult) -> bool:
    parts = _path_parts(parsed)
    return len(parts) >= 4 and parts[0] == "profile" and bool(parts[1]) and parts[2] == "post" and bool(parts[3])


def _valid_mastodon_status_url(parsed: ParseResult) -> bool:
    parts = _path_parts(parsed)
    if len(parts) >= 2 and parts[0].startswith("@") and MASTODON_STATUS_ID_RE.match(parts[1]):
        return True
    if (
        len(parts) >= 4
        and parts[0] == "users"
        and parts[2] == "statuses"
        and MASTODON_STATUS_ID_RE.match(parts[3])
    ):
        return True
    return (
        len(parts) >= 3
        and parts[0] == "web"
        and parts[1] == "statuses"
        and bool(MASTODON_STATUS_ID_RE.match(parts[2]))
    )


def _valid_threads_post_url(parsed: ParseResult) -> bool:
    parts = _path_parts(parsed)
    if len(parts) >= 3 and parts[0].startswith("@") and parts[1] == "post":
        return bool(THREADS_POST_ID_RE.match(parts[2]))
    return len(parts) >= 2 and parts[0] == "t" and bool(THREADS_POST_ID_RE.match(parts[1]))


def _valid_hn_item_url(parsed: ParseResult) -> bool:
    item_id = (parse_qs(parsed.query).get("id") or [""])[0]
    return parsed.path.strip("/") == "item" and bool(NUMERIC_ID_RE.match(item_id))


def _valid_telegram_post_url(parsed: ParseResult) -> bool:
    parts = _path_parts(parsed)
    channel = ""
    message_id = ""
    if len(parts) >= 3 and parts[0] == "s":
        channel, message_id = parts[1], parts[2]
    elif len(parts) >= 2:
        channel, message_id = parts[0], parts[1]
    return bool(TELEGRAM_CHANNEL_RE.match(channel or "") and NUMERIC_ID_RE.match(message_id or ""))
