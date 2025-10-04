# build_xml.py (robust)
import os, time, sys, json
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

API = "https://api.judge.me/api/v1/widgets/all_reviews_page"

SHOP_DOMAIN      = os.getenv("SHOP_DOMAIN", "").strip()
JDGM_PUBLIC_TOKEN= os.getenv("JDGM_PUBLIC_TOKEN", "").strip()
REVIEW_TYPE      = os.getenv("REVIEW_TYPE", "product-reviews").strip()  # product-reviews | shop-reviews | both
PAGE_START       = int(os.getenv("PAGE_START", "1"))
PAGE_LIMIT       = int(os.getenv("PAGE_LIMIT", "9999"))
# chấp nhận truyền ms hoặc giây
_delay = os.getenv("PAGE_DELAY_MS", "200")
try:
    PAGE_DELAY = float(_delay) / 1000.0
except:
    PAGE_DELAY = float(os.getenv("PAGE_DELAY_S", "0.2"))

def dbg(msg):
    print(f"[DEBUG] {msg}")

if not SHOP_DOMAIN or not JDGM_PUBLIC_TOKEN:
    print("Missing SHOP_DOMAIN or JDGM_PUBLIC_TOKEN")
    sys.exit(1)

def fetch_page(page, review_type):
    params = {
        "shop_domain": SHOP_DOMAIN,
        "api_token": JDGM_PUBLIC_TOKEN,
        "page": str(page),
        "review_type": review_type
    }
    r = requests.get(API, params=params, timeout=60)
    ct = (r.headers.get("content-type") or "").lower()
    dbg(f"GET {r.url} -> {r.status_code} ({ct})")
    r.raise_for_status()
    if "application/json" in ct:
        j = r.json()
        html = str(j.get("html") or j.get("widget") or j.get("data") or "")
        return html
    return r.text

def extract_reviews(html, debug_dump=None):
    """Trích các thẻ review dựa trên class jdgm-rev (BeautifulSoup)."""
    if debug_dump:
        Path(debug_dump).write_text(html, encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html or "", "lxml")
    # Nhiều theme bọc danh sách trong .jdgm-all-reviews__reviews hoặc .jdgm-reviews__body
    reviews = soup.select(".jdgm-rev")
    return reviews

def parse_review(node, rtype):
    # node là Tag (.jdgm-rev)
    def text(sel_list):
        for sel in sel_list:
            el = node.select_one(sel)
            if el and el.get_text(strip=True):
                return el.get_text(" ", strip=True)
        return ""
    rating = ""
    el = node.select_one("[data-rating]")
    if el and el.has_attr("data-rating"):
        rating = el["data-rating"].strip()
    if not rating:
        aria = node.get("aria-label") or ""
        if aria:
            import re
            m = re.search(r"([1-5])\s*star", aria, re.I)
            rating = m.group(1) if m else ""
    title  = text([".jdgm-rev__title"])
    body   = text([".jdgm-rev__body",".jdgm-rev__content"])
    author = text([".jdgm-rev__author"])
    date   = text([".jdgm-rev__timestamp",".jdgm-rev__date"])
    prod   = node.select_one(".jdgm-rev__prod-link, .jdgm-rev__product")
    product_title = prod.get_text(" ", strip=True) if prod else ""
    product_url = ""
    link = node.select_one(".jdgm-rev__prod-link")
    if link and link.has_attr("href"): product_url = link["href"]
    photos = [img.get("src","") for img in node.select("img") if img.get("src")]
    return {
        "type": "shop" if rtype=="shop-reviews" else "product",
        "rating": rating or "",
        "title": title, "body": body, "author": author,
        "created_at": date, "product_title": product_title,
        "product_url": product_url, "photos": photos
    }

def crawl_one_type(rtype, out_debug_dir):
    allrows = []
    for p in range(PAGE_START, PAGE_LIMIT+1):
        html = fetch_page(p, rtype)
        # lưu trang 1 để debug nếu cần
        debug_dump = None
        if p == 1:
            debug_dump = out_debug_dir / f"raw_page1_{rtype}.html"
        nodes = extract_reviews(html, debug_dump=str(debug_dump) if debug_dump else None)
        count = len(nodes)
        dbg(f"{rtype} page {p}: found {count} .jdgm-rev")
        if count == 0:
            break
        parsed = [parse_review(n, rtype) for n in nodes]
        allrows.extend(parsed)
        time.sleep(PAGE_DELAY)
    return allrows

def build_xml(rows):
    root = ET.Element("reviews", attrib={"generated_at": datetime.utcnow().isoformat(), "total": str(len(rows))})
    for r in rows:
        rev = ET.SubElement(root, "review", attrib={"type": r["type"], "rating": str(r["rating"])})
        def add(tag,val): ET.SubElement(rev, tag).text = val or ""
        add("title", r["title"]); add("body", r["body"]); add("author", r["author"])
        add("created_at", r["created_at"]); add("product_title", r["product_title"]); add("product_url", r["product_url"])
        if r["photos"]:
            photos = ET.SubElement(rev, "photos")
            for u in r["photos"]: ET.SubElement(photos, "photo").text = u
    return ET.ElementTree(root)

def main():
    out_dir = Path("dist"); out_dir.mkdir(exist_ok=True)
    dbg_dir = out_dir / "debug"; dbg_dir.mkdir(exist_ok=True)

    types = ["product-reviews","shop-reviews"] if REVIEW_TYPE=="both" else [REVIEW_TYPE]
    allrows = []
    for t in types:
        rows = crawl_one_type(t, dbg_dir)
        allrows.extend(rows)

    # nếu vẫn 0 → ghi chú cho bạn kiểm tra raw_page1_*.html
    tree = build_xml(allrows)
    out = out_dir / "jdgm-reviews.xml"
    tree.write(out, encoding="utf-8", xml_declaration=True)
    print(f"✓ wrote {out} with {len(allrows)} reviews")
    if len(allrows) == 0:
        print("NOTE: 0 reviews. Please open dist/debug/raw_page1_product-reviews.html (và shop-reviews nếu có) để xem response thật.")
        # tạo file marker
        (out_dir / "ZERO_REVIEWS.txt").write_text(
            "No .jdgm-rev found. Check dist/debug/raw_page1_*.html and verify SHOP_DOMAIN & JDGM_PUBLIC_TOKEN.",
            encoding="utf-8"
        )

if __name__ == "__main__":
    main()
