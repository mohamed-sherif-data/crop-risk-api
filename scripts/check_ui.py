"""Drive the interface like a farmer would, and assert the outcome.

    py -m uvicorn api.main:app --port 8100
    py scripts/check_ui.py [base_url]

Covers: default state, the four inputs, the report contents, the chart, an
unknown crop, the phone layout and console errors.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8100"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not condition:
        failures.append(label)


def wait_ready(page) -> None:
    page.set_default_timeout(120_000)
    page.wait_for_function(
        "document.getElementById('status').dataset.state === 'ready'", timeout=120_000
    )


def run_report(page) -> None:
    page.click("#calculate")
    page.wait_for_function("!document.getElementById('result').hidden", timeout=120_000)
    page.wait_for_timeout(700)


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)

        page = browser.new_page(viewport={"width": 1440, "height": 900})
        broken: list[str] = []
        page.on(
            "response",
            lambda r: broken.append(f"{r.status} {r.url}")
            if r.status >= 400 and "Nonexistent" not in r.url
            else None,
        )

        page.goto(BASE, wait_until="networkidle")
        wait_ready(page)

        check("it opens on Wheat", page.input_value("#crop-input") == "Wheat")
        check("the report is hidden until asked", page.is_hidden("#result"))
        check("all four soils are offered", page.locator("#soil-group .segmented__item").count() == 4)
        check("all four stages are offered", page.locator("#stage-group .segmented__item").count() == 4)
        check("water starts at full supply", page.inner_text("#water-value") == "100 %")

        # The notebook's Farmer B: sandy soil, 70 % water, flowering.
        page.click("#soil-group [data-value='Sandy']")
        page.fill("#water-input", "70")
        page.dispatch_event("#water-input", "input")
        check("the water read-out follows the slider", page.inner_text("#water-value") == "70 %")
        run_report(page)

        check("the score matches the notebook", page.inner_text("#score-value") == "31.9", page.inner_text("#score-value"))
        check("the band is medium", page.inner_text("#score-chip").lower() == "medium risk")
        check("expected yield reflects the water loss", page.inner_text("#expected-value") == "5,456")
        check("the drop probability is shown", page.inner_text("#drop-value").startswith("7.4"))
        check("the score breakdown lists three parts", page.locator("#bars .bars__row").count() == 3)
        check("the drivers are explained", page.locator("#drivers .drivers__row").count() == 3)
        check(
            "the driver bars have real width",
            page.evaluate(
                "() => [...document.querySelectorAll('.drivers__fill')]"
                ".every(el => el.getBoundingClientRect().height >= 4)"
            ),
        )
        check("the chart is drawn", page.evaluate("() => window.Chart.getChart('chart')?.data.labels.length || 0") > 30)
        check("the assumptions stay visible", "assumption" in page.inner_text(".assumptions").lower())

        # A different crop recomputes rather than reusing the previous answer.
        page.fill("#crop-input", "Rice")
        page.click("#soil-group [data-value='Clay']")
        run_report(page)
        check("choosing another crop recalculates", "Rice" in page.inner_text("#chart-caption"))

        # A crop that does not exist must not silently return the old answer.
        page.fill("#crop-input", "Not-a-crop")
        page.click("#calculate")
        page.wait_for_timeout(600)
        check(
            "an unknown crop is refused",
            "not in the list" in page.inner_text("#crop-hint"),
            page.inner_text("#crop-hint")[:60],
        )

        # ------------------------------------------------------------- mobile
        phone = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        phone.goto(BASE, wait_until="networkidle")
        wait_ready(phone)
        run_report(phone)

        check(
            "nothing overflows the phone viewport",
            phone.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1,
        )
        check("the phone report is filled", phone.inner_text("#score-value") not in {"", "—"})
        check("no failed requests", not broken, "; ".join(broken[:2]))

        browser.close()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
