import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = "https://players.pokemon-card.com"
LIST_URL = f"{BASE_URL}/event/result/list"

SEASON_START = "2026-09-26"
OUTPUT = Path("data/index.json")

JST = timezone(timedelta(hours=9))


def extract_date(text: str):
    patterns = [
        r"(20\d{2})/(\d{1,2})/(\d{1,2})",
        r"(20\d{2})-(\d{1,2})-(\d{1,2})",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return (
                f"{int(m.group(1)):04d}-"
                f"{int(m.group(2)):02d}-"
                f"{int(m.group(3)):02d}"
            )

    return None


async def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page(
            locale="ja-JP",
            viewport={"width": 1440, "height": 1800},
        )

        print("Opening official result list...")
        await page.goto(
            LIST_URL,
            wait_until="domcontentloaded",
            timeout=120000,
        )

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=30000,
            )
        except Exception:
            pass

        await page.wait_for_timeout(5000)

        # 遅延描画・スクロール読み込み対策
        previous_count = -1

        for _ in range(15):
            links = page.locator(
                'a[href*="/event/detail/"][href*="/result"]'
            )

            count = await links.count()

            if count == previous_count:
                break

            previous_count = count

            await page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)"
            )

            await page.wait_for_timeout(1500)

        links = page.locator(
            'a[href*="/event/detail/"][href*="/result"]'
        )

        count = await links.count()

        print(f"Result links found: {count}")

        events = {}

        for i in range(count):
            link = links.nth(i)

            href = await link.get_attribute("href")

            if not href:
                continue

            match = re.search(
                r"/event/detail/(\d+)/result",
                href,
            )

            if not match:
                continue

            event_id = match.group(1)

            text_candidates = []

            for level in range(1, 6):
                try:
                    parent = link.locator(
                        f"xpath=ancestor::div[{level}]"
                    )

                    if await parent.count():
                        text = (
                            await parent.first.inner_text()
                        ).strip()

                        if text:
                            text_candidates.append(text)

                except Exception:
                    pass

            try:
                own_text = (await link.inner_text()).strip()
                if own_text:
                    text_candidates.append(own_text)
            except Exception:
                pass

            # シティリーグと判断できる最も近いテキストを採用
            event_text = ""

            for text in text_candidates:
                if "シティリーグ" in text:
                    event_text = text
                    break

            if not event_text:
                continue

            event_date = extract_date(event_text)

            if (
                event_date is not None
                and event_date < SEASON_START
            ):
                continue

            if href.startswith("http"):
                result_url = href
            else:
                result_url = BASE_URL + href

            events[event_id] = {
                "event_id": event_id,
                "date": event_date,
                "text": event_text,
                "result_url": result_url,
            }

        body_text = ""

        try:
            body_text = (
                await page.locator("body").inner_text()
            )[:10000]
        except Exception:
            pass

        data = {
            "season": "2027-S1",
            "season_start": SEASON_START,
            "source": LIST_URL,
            "updated_at": datetime.now(
                JST
            ).isoformat(timespec="seconds"),
            "event_count": len(events),
            "events": sorted(
                events.values(),
                key=lambda x: (
                    x.get("date") or "",
                    x["event_id"],
                ),
                reverse=True,
            ),
            "debug": {
                "page_title": await page.title(),
                "raw_result_link_count": count,
                "body_text_head": body_text,
            },
        }

        OUTPUT.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            f"City League results found: "
            f"{len(events)}"
        )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
