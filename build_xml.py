import os, re, time, sys
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

API = "https://api.judge.me/api/v1/widgets/all_reviews_page"

SHOP_DOMAIN = os.getenv("SHOP_DOMAIN", "").strip()
JDGM_PUBLIC_TOKEN = os.getenv("JDGM_PUBLIC_TOKEN", "").strip()
REVIEW_TYPE = os.getenv("REVIEW_TYPE", "product-reviews").strip()  # product-reviews | shop-reviews | both
PAGE_START = int(float(os.getenv("PAGE_START", "1")))
PAGE_LIMIT = int(float(os.getenv("PAGE_LIMIT", "9999")))           # đủ lớn để vét hết
PAGE_DELAY = float(os.getenv("PAGE_DELAY_MS", "0.2")) / 1000.0 if float(os.getenv("PAGE_DELAY_MS","200")).is_integer() else float(os.getenv("PAGE_DELAY_MS","0.2"))

if not SHOP_DOMAIN or not JDGM_PUBLIC_TOKEN:
    print("Missing SHOP_DOMAIN or JDGM_PUBLIC_TOKEN")
    sys.exit(1)

def fetch_page(page, review_type):
    r = requests.get(API, params={
        "shop_domain": SHOP_DOMAIN,
        "api_token": JDGM_PUBLIC_TOKEN,
        "page": str(page),
        "review_type": review_type
    }, timeout=60)
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        j = r.json()
        return str(j.get("html") or j.get("widget") or j.get("data") or "")
    return r.text

REV_BLOCK = re.compile(r'<div[^>]*class="[^"]*jdgm-rev[^"]*"[^>]*>.*?</div>', re.I|re.S)

def extract_blocks(html):
    return REV_BLOCK.findall(html) if html else []

def parse_block(block, rtype):
    s = BeautifulSoup(block, "lxml")
    rating = ""
    el = s.select_one("[data-rating]")
    if el and el.has_attr("data-rating"):
        rating = el["data-rating"].strip()
    if not rating:
        el = s.find(attrs={"aria-label": re.compile("star", re.I)})
        if el and el.has_attr("aria-label"):
            m = re.search(r"([1-5])\s*star", el["aria-label"], re.I)
            rating = m.group(1) if m else ""
    def txt(sels):
        for ss in sels:
            e = s.select_one(ss)
            if e and e.get_text(strip=True): return e.get_text(" ", strip=True)
        return ""
    title  = txt([".jdgm-rev__title"])
    body   = txt([".jdgm-rev__body", ".jdgm-rev__content"])
    author = txt([".jdgm-rev__author"])
    date   = txt([".jdgm-rev__timestamp", ".jdgm-rev__date"])
    prod   = s.select_one(".jdgm-rev__prod-link, .jdgm-rev__product")
    product_title = prod.get_text(" ", strip=True) if prod else ""
    product_url = (s.select_one(".jdgm-rev__prod-link") or {}).get("href","")
    photos = [img.get("src") for img in s.select("img") if img.get("src")]
    return {
        "type": "shop" if rtype=="shop-reviews" else "product",
        "rating": rating, "title": title, "body": body, "author": author,
        "created_at": date, "product_title": product_title,
        "product_url": product_url, "photos": photos
    }

def crawl(rtype):
    allrows = []
    for p in range(PAGE_START, PAGE_LIMIT+1):
        html = fetch_page(p, rtype)
        blocks = extract_blocks(html)
        if not blocks:
            print(f"[{rtype}] no more at page {p}")
            break
        parsed = [parse_block(b, rtype) for b in blocks]
        allrows += parsed
        print(f"[{rtype}] page {p}: +{len(parsed)} (total {len(allrows)})")
        time.sleep(PAGE_DELAY)
    return allrows

def build_xml(rows):
    root = ET.Element("reviews", attrib={"generated_at": datetime.utcnow().isoformat(), "total": str(len(rows))})
    for r in rows:
        rev = ET.SubElement(root, "review", attrib={"type": r["type"], "rating": str(r["rating"] or "")})
        def add(tag,val): ET.SubElement(rev, tag).text = val or ""
        add("title", r["title"]); add("body", r["body"]); add("author", r["author"])
        add("created_at", r["created_at"]); add("product_title", r["product_title"]); add("product_url", r["product_url"])
        if r["photos"]:
            photos = ET.SubElement(rev, "photos")
            for u in r["photos"]: ET.SubElement(photos, "photo").text = u
    return ET.ElementTree(root)

def main():
    types = ["product-reviews","shop-reviews"] if REVIEW_TYPE=="both" else [REVIEW_TYPE]
    rows = []
    for t in types: rows += crawl(t)
    Path("dist").mkdir(exist_ok=True)
    out = Path("dist") / "jdgm-reviews.xml"
    build_xml(rows).write(out, encoding="utf-8", xml_declaration=True)
    print(f"✓ wrote {out} with {len(rows)} reviews")

if __name__ == "__main__":
    main()
