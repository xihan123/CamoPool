import unittest

from camopool.profiles import Platform, detect_platform, resolve_profile


class ProfileTests(unittest.TestCase):
    def test_detects_supported_platforms(self):
        cases = {
            "https://mp.weixin.qq.com/s/abc": Platform.WECHAT,
            "https://www.kuaishou.com/short-video/abc": Platform.KUAISHOU,
            "https://v.douyin.com/abc": Platform.DOUYIN,
            "https://www.xiaohongshu.com/explore/abc": Platform.XIAOHONGSHU,
            "https://m.baidu.com/s?wd=test": Platform.BAIDU,
            "https://www.toutiao.com/article/123": Platform.TOUTIAO,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_platform(url), expected)

    def test_does_not_match_domain_suffix_attack(self):
        self.assertEqual(detect_platform("https://douyin.com.example.org"), Platform.GENERIC)

    def test_explicit_platform_overrides_detection(self):
        profile = resolve_profile("https://example.com", Platform.WECHAT)
        self.assertEqual(profile.platform, Platform.WECHAT)
        self.assertIn("MicroMessenger", profile.user_agent)

    def test_generic_uses_browserforge_generated_firefox_ua(self):
        self.assertIsNone(resolve_profile("https://example.com").user_agent)

    def test_app_profile_builds_global_camoufox_config(self):
        config = resolve_profile("https://mp.weixin.qq.com/").camoufox_config()
        self.assertIn("MicroMessenger", config["navigator.userAgent"])
        self.assertEqual(config["screen.width"], 412)


if __name__ == "__main__":
    unittest.main()
