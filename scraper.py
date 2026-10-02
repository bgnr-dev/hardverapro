#!/usr/bin/env python3
"""Hardverapró figyelő — cron-barát új-hirdetés értesítő."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

DIR = Path(__file__).resolve().parent
KEYWORDS_FILE = DIR / "keywords.txt"
STATE_FILE = DIR / "state.json"
SEARCH_URL = "https://hardverapro.hu/aprok/keres.php"
COOLDOWN_SEC = 2
MAX_PRICE_RE = re.compile(r"\[([^\[\]]+)\]")


def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def digits_only(text: str) -> int | None:
    """'65 000 Ft' / '65000' / '65.000' → int, vagy None."""
    d = re.sub(r"\D", "", text or "")
    return int(d) if d else None


def parse_price_limit(text: str) -> int | None:
    """Max ár: 65000, 65 000, 80k, 80e (=80000)."""
    t = (text or "").strip().lower().replace("\xa0", "")
    t = re.sub(r"\s+", "", t)
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)([ke])", t)
    if m:
        num = float(m.group(1).replace(",", "."))
        return int(num * 1000)
    return digits_only(t)


def extract_max_price(line: str) -> tuple[str, int | None]:
    """Kiszed [65000] / [65 000] / [80k] / [80e] a sorból → (maradék sor, max_ár)."""
    max_price = None

    def _take(m: re.Match) -> str:
        nonlocal max_price
        val = parse_price_limit(m.group(1))
        if val is not None:
            max_price = val
        return " "

    return MAX_PRICE_RE.sub(_take, line), max_price


def term_in_title(term: str, title: str) -> bool:
    """Szótömb-szerű egyezés: 'x570' ne találjon rá az 'rx5700'-ra."""
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])",
            title.lower(),
        )
        is not None
    )


def load_keywords() -> list[dict]:
    """Beolvassa a keywords.txt-et.

    Sorok:
      kifejezés [-kizárt ...] [maxár]
      https://hardverapro.hu/... [kifejezés ...] [-kizárt ...] [maxár]

    Üres sorok és # kommentek kimaradnak.
    """
    if not KEYWORDS_FILE.exists():
        print(
            f"[{ts()}] hiányzik a {KEYWORDS_FILE.name} — másold a keywords.example.txt-et",
            file=sys.stderr,
        )
        return []

    searches = []
    for raw in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        line, max_price = extract_max_price(line)
        line = line.strip()
        if not line:
            continue

        url = None
        parts = line.split()
        if parts[0].startswith("http://") or parts[0].startswith("https://"):
            url = parts[0]
            parts = parts[1:]

        include, exclude = [], []
        for part in parts:
            if part.startswith("-") and len(part) > 1:
                exclude.append(part[1:])
            else:
                include.append(part)

        if not url and not include:
            continue

        key = url or " ".join(include)
        if max_price is not None:
            key = f"{key}|max={max_price}"
        searches.append(
            {
                "key": key,
                "url": url,
                "terms": include,
                "exclude": exclude,
                "max_price": max_price,
            }
        )
    return searches


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_search_url(terms: list[str], max_price: int | None = None) -> str:
    # A böngészős keresőform paraméterei (checkbox párok)
    params = [
        ("stext", " ".join(terms)),
        ("stcid_text", ""),
        ("stcid", ""),
        ("stmid_text", ""),
        ("stmid", ""),
        ("minprice", ""),
        ("maxprice", str(max_price) if max_price is not None else ""),
        ("cmpid_text", ""),
        ("cmpid", ""),
        ("usrid_text", ""),
        ("usrid", ""),
        ("__buying", "0"),  # csak eladó hirdetések (ne „Keresek:”)
        ("__brandnew", "1"),
        ("__brandnew", "0"),
        ("stext_none", ""),
    ]
    return f"{SEARCH_URL}?{urlencode(params)}"


class Browser:
    """Headless Chrome — a kereső cookie-consent nélkül indexre dobja a botokat."""

    def __init__(self) -> None:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1400,900")
        opts.add_argument(
            "user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        self.driver = webdriver.Chrome(options=opts)
        self._ready = False

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def _accept_consent(self) -> None:
        try:
            btn = self.driver.find_element(By.CSS_SELECTOR, 'button[mode="primary"]')
            if btn.is_displayed():
                btn.click()
                time.sleep(1.5)
                return
        except Exception:
            pass
        self.driver.execute_script(
            """
            for (const b of document.querySelectorAll('button')) {
              const t = (b.innerText || '').trim();
              if (/elfogad|accept/i.test(t)) { b.click(); return; }
            }
            """
        )
        time.sleep(1)

    def ensure_ready(self) -> None:
        if self._ready:
            return
        self.driver.get("https://hardverapro.hu/")
        time.sleep(2)
        self._accept_consent()
        self._ready = True

    def fetch(self, url: str) -> tuple[str, str]:
        self.ensure_ready()
        self.driver.get(url)
        time.sleep(3)
        return self.driver.current_url, self.driver.page_source


def parse_ads(
    html: str,
    terms: list[str],
    exclude: list[str],
    max_price: int | None = None,
) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    ads, seen = [], set()

    for box in soup.select(".uad-col-title"):
        a = box.select_one('h1 > a[href*="/apro/"]')
        if not a:
            continue
        href = a.get("href", "")
        if "/apro/" not in href:
            continue
        ad_id = href.split("/apro/", 1)[1].split("/", 1)[0]
        if not ad_id or ad_id in seen:
            continue
        seen.add(ad_id)

        title = a.get_text(strip=True)
        if not title:
            continue

        posted = ""
        t = box.select_one(".uad-time time")
        if t:
            posted = t.get_text(strip=True)
        if "előresorol" in posted.lower():
            posted = "kiemelt"

        if any(term_in_title(x, title) for x in exclude):
            continue
        if terms and not all(term_in_title(term, title) for term in terms):
            continue

        price_el = box.select_one(".uad-price span.text-nowrap")
        price = price_el.get_text(strip=True) if price_el else "N/A"
        if max_price is not None:
            pv = digits_only(price)
            if pv is None or pv > max_price:
                continue

        link = href if href.startswith("http") else f"https://hardverapro.hu{href}"
        parent = box.parent
        iced = bool(
            parent
            and "uad-status-iced" in (parent.get("class") or [])
        ) or bool(box.select_one(".uad-price-iced"))

        ads.append(
            {
                "id": ad_id,
                "title": title,
                "price": price,
                "link": link,
                "posted": posted,
                "iced": iced,
            }
        )
    return ads


def fmt_ad(ad: dict) -> str:
    posted = (ad.get("posted") or "N/A")[:10]
    title = ad["title"]
    if ad.get("iced"):
        title = f"🧊 {title}"
    return f"  {posted:>10} | {ad['price']:>15} | {title[:80]} | {ad['link']}"


def render_html_report(
    groups: list[tuple[str, list[dict]]], *, all_matches: bool = False
) -> str:
    """Kompakt, e-mail-barát HTML az új hirdetésekről (vagy teljes snapshot)."""
    esc = html_lib.escape
    total = sum(len(ads) for _, ads in groups)
    when = ts()
    kind = "találat" if all_matches else "új"
    th = (
        "padding:6px 10px 6px 0;font-size:11px;font-weight:650;"
        "color:#888;text-transform:uppercase;letter-spacing:.04em;"
        "border-bottom:1px solid #e5e5e5;text-align:left"
    )
    td_date = (
        "padding:7px 10px 7px 0;white-space:nowrap;vertical-align:top;"
        "font-size:12px;color:#888"
    )
    td_price = (
        "padding:7px 10px;white-space:nowrap;vertical-align:top;font-weight:650"
    )
    td_title = "padding:7px 0;vertical-align:top"
    out: list[str] = [
        "<!DOCTYPE html>",
        '<html><head><meta charset="utf-8">',
        f"<title>Hardverapró — {total} {kind}</title></head>",
        '<body style="margin:0;padding:16px;font:14px/1.45 -apple-system,'
        "BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a1a;"
        'background:#f4f5f7">',
        '<div style="max-width:640px;margin:0 auto;background:#fff;'
        'border-radius:8px;padding:20px 18px 24px;'
        'box-shadow:0 1px 3px rgba(0,0,0,.08)">',
        f'<p style="margin:0 0 4px;font-size:18px;font-weight:650;'
        f'text-align:center">Hardverapró — {total} {kind}</p>',
        f'<p style="margin:0 0 8px;font-size:12px;color:#888;text-align:center">'
        f"{esc(when)}</p>",
    ]
    for name, ads in groups:
        out.append(
            f'<div style="margin:28px 0 14px;padding:14px 12px;'
            f'background:#f7f8fa;border-radius:6px;text-align:center">'
            f'<div style="font-size:17px;font-weight:700;letter-spacing:.01em;'
            f'line-height:1.3">{esc(name)}</div>'
            f'<div style="margin-top:4px;font-size:12px;color:#888">'
            f"{len(ads)} {kind}</div></div>"
        )
        out.append('<table style="width:100%;border-collapse:collapse">')
        out.append(
            "<tr>"
            f'<th style="{th}">Dátum</th>'
            f'<th style="{th}">Ár</th>'
            f'<th style="{th}">Cím</th>'
            "</tr>"
        )
        for ad in ads:
            posted = esc((ad.get("posted") or "—")[:12])
            price = esc(ad.get("price") or "N/A")
            title = esc(ad.get("title") or "")
            link = esc(ad.get("link") or "#", quote=True)
            badge = ""
            if ad.get("iced"):
                badge = (
                    '<span style="display:inline-block;margin-right:6px;padding:1px 5px;'
                    "border-radius:3px;background:#e3f2fd;vertical-align:middle\" "
                    'title="jégelt">🧊</span>'
                )
            out.append(
                "<tr>"
                f'<td style="{td_date}">{posted}</td>'
                f'<td style="{td_price}">{price}</td>'
                f'<td style="{td_title}">{badge}'
                f'<a href="{link}" style="color:#0b57d0;text-decoration:none">'
                f"{title}</a></td>"
                "</tr>"
            )
        out.append("</table>")
    out.append("</div></body></html>")
    return "\n".join(out)


def label(s: dict) -> str:
    if s["terms"]:
        name = " ".join(s["terms"])
    elif s["url"]:
        name = s["url"].split("hardverapro.hu", 1)[-1][:50]
    else:
        name = "?"
    if s.get("max_price") is not None:
        name = f"{name} ≤{s['max_price']:,}".replace(",", " ")
    return name


def run(monitor: bool, show_all: bool) -> int:
    searches = load_keywords()
    if not searches:
        return 1

    state = load_state()
    found_new = False
    monitor_groups: list[tuple[str, list[dict]]] = []
    browser = Browser()

    if not monitor:
        print(f"[{ts()}] Hardverapró figyelő")

    try:
        for i, s in enumerate(searches):
            url = s["url"] or build_search_url(s["terms"], s.get("max_price"))
            name = label(s)
            excl = f" (kizárva: {', '.join(s['exclude'])})" if s["exclude"] else ""

            try:
                final_url, page_html = browser.fetch(url)
                ads = parse_ads(page_html, s["terms"], s["exclude"], s.get("max_price"))
            except Exception as e:
                print(f"[{ts()}] HIBA '{name}': {e}", file=sys.stderr)
                continue

            redirected = (
                not s["url"]
                and "keres.php" in url
                and "keres.php" not in final_url
            )
            if redirected and not ads and not monitor:
                print(
                    f"[{ts()}] '{name}'{excl}… FIGYELEM: a kereső átirányított ide: "
                    f"{final_url.split('hardverapro.hu', 1)[-1]} — "
                    f"fogadd el a cookie ablakot / ellenőrizd a ChromeDriver-t"
                )

            prev = set(state.get(s["key"], []))
            curr = {a["id"] for a in ads}
            new_ads = [a for a in ads if a["id"] not in prev]
            # Látott ID-k gyűjtése (ne töröljük, ha egy hirdetés átmenetileg kiesik a listából)
            state[s["key"]] = sorted(prev | curr)

            if monitor:
                # -m: csak új; -a -m: minden aktuális találat (teszt / teljes snapshot e-mail)
                report_ads = ads if show_all else new_ads
                if report_ads:
                    monitor_groups.append((name, report_ads))
                if new_ads:
                    found_new = True
            elif show_all:
                tag = f"{len(new_ads)} új / " if new_ads else ""
                print(f"[{ts()}] '{name}'{excl}… {tag}{len(ads)} találat")
                new_ids = {n["id"] for n in new_ads}
                for ad in ads:
                    mark = "ÚJ  " if ad["id"] in new_ids else "    "
                    print(f"{mark}{fmt_ad(ad).lstrip()}")
                found_new = found_new or bool(new_ads)
            else:
                if new_ads:
                    found_new = True
                    print(
                        f"[{ts()}] '{name}'{excl}… {len(new_ads)} új ({len(ads)} találat)"
                    )
                    for ad in new_ads:
                        print(fmt_ad(ad))
                else:
                    print(f"[{ts()}] '{name}'{excl}… ok, {len(ads)} találat")

            if i < len(searches) - 1:
                time.sleep(COOLDOWN_SEC)
    finally:
        browser.close()
        save_state(state)

    if monitor:
        if monitor_groups:
            print(render_html_report(monitor_groups, all_matches=show_all))
    elif found_new:
        print(f"[{ts()}] Kész — van új hirdetés")
    else:
        print(f"[{ts()}] Kész — nincs új hirdetés")

    return 1 if found_new else 0


def main() -> None:
    p = argparse.ArgumentParser(description="Hardverapro.hu új hirdetések figyelése")
    p.add_argument(
        "-m",
        "--monitor",
        action="store_true",
        help="csendes mód: csak új hirdetésnél ír ki HTML reportot (cron/mail)",
    )
    p.add_argument(
        "-a",
        "--show-all",
        action="store_true",
        help="minden egyező hirdetést kiír; -m-mel: teljes HTML snapshot (nem csak új)",
    )
    args = p.parse_args()
    sys.exit(run(monitor=args.monitor, show_all=args.show_all))


if __name__ == "__main__":
    main()
