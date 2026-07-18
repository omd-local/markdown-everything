"""Shared WeChat URL and verification-page classifiers."""
from __future__ import annotations

from urllib.parse import urlparse

WECHAT_HOSTS = {"mp.weixin.qq.com"}
WECHAT_VERIFICATION_PATH = "/mp/wappoc_appmsgcaptcha"
WECHAT_VERIFICATION_REQUIRED_MESSAGE = (
    "WeChat returned a verification/captcha page instead of article HTML. "
    "Open the link in WeChat or a browser, complete the verification if shown, "
    "then copy the final article URL and retry. Automated fetch cannot pass this challenge."
)
WECHAT_VERIFICATION_MARKERS = (
    "secitptpage/verify",
    "TCaptcha.js",
    WECHAT_VERIFICATION_PATH,
)


def is_wechat_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in WECHAT_HOSTS)


def is_wechat_article_url(url: str) -> bool:
    """Return true for supported WeChat Official Account article URL shapes."""
    parsed = urlparse(url)
    if not is_wechat_url(url):
        return False
    path = parsed.path.rstrip("/")
    return path == "/s" or path.startswith("/s/")


def is_wechat_verification_url(url: str) -> bool:
    parsed = urlparse(url)
    return is_wechat_url(url) and parsed.path.rstrip("/") == WECHAT_VERIFICATION_PATH


def looks_like_verification_page(source_html: str) -> bool:
    return any(marker in source_html for marker in WECHAT_VERIFICATION_MARKERS)
