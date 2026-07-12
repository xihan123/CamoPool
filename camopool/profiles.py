from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit


class Platform(str, Enum):
    AUTO = "auto"
    GENERIC = "generic"
    WECHAT = "wechat"
    KUAISHOU = "kuaishou"
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BAIDU = "baidu"
    TOUTIAO = "toutiao"


@dataclass(frozen=True)
class PlatformProfile:
    platform: Platform
    user_agent: Optional[str]
    domains: Tuple[str, ...] = ()

    def camoufox_config(self) -> Optional[Dict[str, Any]]:
        if not self.user_agent:
            return None
        return {
            "navigator.userAgent": self.user_agent,
            "navigator.platform": "Linux aarch64",
            "navigator.oscpu": "Linux aarch64",
            "navigator.hardwareConcurrency": 8,
            "screen.width": 412,
            "screen.height": 915,
        }


_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; 23127PN0CC Build/UKQ1.231003.002; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.82 "
    "Mobile Safari/537.36"
)

PROFILES = {
    Platform.GENERIC: PlatformProfile(Platform.GENERIC, None),
    Platform.WECHAT: PlatformProfile(
        Platform.WECHAT,
        _ANDROID + " MicroMessenger/8.0.56.2800(0x2800385A) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
        ("weixin.qq.com", "wechat.com", "weixin110.qq.com"),
    ),
    Platform.KUAISHOU: PlatformProfile(
        Platform.KUAISHOU,
        _ANDROID + " Kuaishou/12.6.20.35918 (KPF/ANDROID)",
        ("kuaishou.com", "gifshow.com", "kwai.com"),
    ),
    Platform.DOUYIN: PlatformProfile(
        Platform.DOUYIN,
        _ANDROID + " aweme_310601 JsSdk/1.0 NetType/WIFI Channel/douyin_outer",
        ("douyin.com", "iesdouyin.com", "douyinvod.com"),
    ),
    Platform.XIAOHONGSHU: PlatformProfile(
        Platform.XIAOHONGSHU,
        _ANDROID + " xhs/8.40.0 Discover/8.40.0 Device/Android",
        ("xiaohongshu.com", "xhslink.com", "xhscdn.com"),
    ),
    Platform.BAIDU: PlatformProfile(
        Platform.BAIDU,
        _ANDROID + " baiduboxapp/13.52.0.10 (Baidu; P1 14)",
        ("baidu.com", "baidubce.com", "bdstatic.com"),
    ),
    Platform.TOUTIAO: PlatformProfile(
        Platform.TOUTIAO,
        _ANDROID + " NewsArticle/9.7.0 okhttp/4.10.0",
        ("toutiao.com", "snssdk.com", "toutiaocdn.com"),
    ),
}


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def detect_platform(url: str) -> Platform:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for platform, profile in PROFILES.items():
        if platform is not Platform.GENERIC and any(_matches(host, domain) for domain in profile.domains):
            return platform
    return Platform.GENERIC


def resolve_profile(url: str, requested: Platform = Platform.AUTO) -> PlatformProfile:
    platform = detect_platform(url) if requested is Platform.AUTO else requested
    return PROFILES[platform]
