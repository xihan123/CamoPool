import json
import os
import urllib.request

from playwright.sync_api import Browser, sync_playwright

API = os.getenv("CAMOPOOL_API", "http://localhost:8000")
API_KEY = os.getenv("CAMOPOOL_API_KEY", "change-me")
TARGET = os.getenv("TARGET_URL", "https://example.com/")


def request(path: str, method: str = "GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        API + path,
        method=method,
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response) if response.status != 204 else None


def main():
    lease = request("/v1/leases", "POST", {"url": TARGET})
    try:
        with sync_playwright() as playwright:
            browser: Browser = playwright.firefox.connect(
                lease["ws_endpoint"],
                timeout=30_000,
            )
            page = browser.new_page()
            page.goto(TARGET)
            print(page.title())
            print(page.evaluate("navigator.userAgent"))
            browser.close()
    finally:
        request("/v1/leases/" + lease["lease_id"], "DELETE")


if __name__ == "__main__":
    main()
