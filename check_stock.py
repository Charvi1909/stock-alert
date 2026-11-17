"""
Amul stock checker -> phone push notification (via ntfy).

Opens the product page in a headless browser, sets your delivery pincode,
then reads the stock straight from Amul's own product API response.
Falls back to reading the page text if the API response isn't seen.

Env vars:
  PRODUCT_URL   product page link
  PINCODE       delivery pincode
  NTFY_TOPIC    your secret ntfy topic name (required to send notifications)
  TEST_NOTIFY   "true" -> always send a notification with the current status
"""

import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PRODUCT_URL = os.environ.get(
    "PRODUCT_URL",
    "https://shop.amul.com/en/product/amul-high-protein-plain-lassi-200-ml-or-pack-of-30",
).strip()
PINCODE = os.environ.get("PINCODE", "632014").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
TEST_NOTIFY = os.environ.get("TEST_NOTIFY", "").strip().lower() == "true"
CHROME_PATH = os.environ.get("CHROME_PATH")  # only for local testing

SLUG = PRODUCT_URL.rstrip("/").split("/product/")[-1].split("?")[0]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def notify(title: str, message: str, priority: str = "urgent", tags: str = "rotating_light"):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set - skipping notification")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Click": PRODUCT_URL,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        print(f"Notification sent ({r.status})")


def set_pincode(page) -> bool:
    """Fill Amul's 'Select Delivery Pincode' popup and pick the suggestion."""
    try:
        page.wait_for_selector("#search", state="visible", timeout=15000)
    except PWTimeout:
        print("Pincode box not visible")
        return False
    page.click("#search")
    page.fill("#search", PINCODE)
    try:
        page.click("#automatic a.searchitem-name", timeout=8000)
    except PWTimeout:
        try:
            page.click(f"text={PINCODE}", timeout=4000)
        except PWTimeout:
            print("No pincode suggestion appeared (pincode may not be serviceable)")
            return False
    page.wait_for_timeout(3000)
    return True


def check() -> tuple[str, str]:
    """Returns (status, detail) where status is 'in', 'out' or 'unknown'."""
    captured = {}

    def on_response(resp):
        if "/api/1/entity/ms.products" not in resp.url or resp.status != 200:
            return
        try:
            data = resp.json().get("data") or []
        except Exception:
            return
        for item in data:
            if item.get("alias") == SLUG:
                captured["item"] = item

    with sync_playwright() as p:
        launch = {"headless": True}
        if CHROME_PATH:
            launch["executable_path"] = CHROME_PATH
        browser = p.chromium.launch(**launch)
        page = browser.new_page(user_agent=UA, viewport={"width": 1280, "height": 900})
        page.on("response", on_response)

        page.goto(PRODUCT_URL, wait_until="networkidle", timeout=60000)
        ok = set_pincode(page)
        print(f"Pincode {PINCODE} set: {ok}")

        # Reload so the product data is fetched for this pincode's store
        captured.clear()
        page.goto(PRODUCT_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        page.screenshot(path="page.png", full_page=False)
        body = page.inner_text("body")
        browser.close()

    if not ok:
        return "unknown", "Couldn't set the pincode on Amul's site"

    item = captured.get("item")
    if item is not None:
        available = item.get("available") or 0
        qty = item.get("inventory_quantity") or 0
        detail = f"API: available={available}, inventory_quantity={qty}"
        return ("in" if available > 0 and qty > 0 else "out"), detail

    # Fallback: page text
    if "Sold Out" in body or "Out of Stock" in body:
        return "out", "Page text says Sold Out / Out of Stock"
    if "Add to Cart" in body:
        return "in", "Page shows Add to Cart and no Sold Out label"
    return "unknown", "Couldn't find stock info on the page"


def main():
    print(f"Checking {SLUG} for pincode {PINCODE}")
    try:
        status, detail = check()
    except Exception as e:
        status, detail = "unknown", f"Error: {e}"
    print(f"Result: {status} ({detail})")

    if status == "in":
        notify("Amul lassi is IN STOCK!", f"Back in stock for {PINCODE}. Tap to buy now.")
    elif TEST_NOTIFY:
        label = {"out": "still out of stock", "unknown": "check FAILED"}[status]
        notify(f"Test: Amul lassi {label}", f"Pincode {PINCODE}. {detail}",
               priority="default", tags="test_tube")

    # A failed check fails the GitHub run, so GitHub emails you if it keeps breaking
    sys.exit(1 if status == "unknown" else 0)


if __name__ == "__main__":
    main()
