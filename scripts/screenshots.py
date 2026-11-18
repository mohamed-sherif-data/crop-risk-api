"""Capture the interface screenshots used in the docs.

    py scripts/screenshots.py [base_url] [subfolder]

Pinned viewports (1440×900 and 390×844, dpr 1) so the images are reproducible.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8100"
SUBDIR = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = Path(__file__).resolve().parents[1] / "docs" / "media" / SUBDIR


def ready(page) -> None:
    page.set_default_timeout(180_000)
    page.wait_for_function(
        "document.getElementById('status').dataset.state === 'ready'", timeout=180_000
    )


def farmer_b(page) -> None:
    page.click("#soil-group [data-value='Sandy']")
    page.fill("#water-input", "70")
    page.dispatch_event("#water-input", "input")
    page.click("#calculate")
    page.wait_for_function("!document.getElementById('result').hidden", timeout=180_000)
    page.wait_for_timeout(1200)


def freeze_chart(page) -> None:
    """Draw the chart without its resize animation, so a full-page shot is not
    captured mid-transition (the canvas stretches with the viewport)."""
    page.evaluate(
        """() => {
            if (!window.Chart) return;
            Chart.defaults.animation = false;
            const chart = Chart.getChart('chart');
            if (chart) { chart.options.animation = false; chart.resize(); chart.update('none'); }
        }"""
    )
    page.wait_for_timeout(500)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)

        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.goto(BASE, wait_until="networkidle")
        ready(page)
        page.screenshot(path=OUT / "ui-hero.png")
        page.locator("#form-card").screenshot(path=OUT / "ui-form.png")

        farmer_b(page)
        freeze_chart(page)
        page.add_style_tag(content=".topbar, .status { display: none !important; }")
        page.wait_for_timeout(150)
        page.screenshot(path=OUT / "ui-result.png", full_page=True)
        page.evaluate("document.querySelectorAll('style').forEach(s => s.remove())")

        phone = browser.new_page(
            viewport={"width": 390, "height": 844}, device_scale_factor=1, is_mobile=True, has_touch=True
        )
        phone.goto(BASE, wait_until="networkidle")
        ready(phone)
        farmer_b(phone)
        freeze_chart(phone)
        phone.screenshot(path=OUT / "ui-phone.png", full_page=True)

        browser.close()
        print(f"wrote 4 images to {OUT}")


if __name__ == "__main__":
    main()
