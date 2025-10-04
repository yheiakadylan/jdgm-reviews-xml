# build_xml.py — build 1 file: dist/jdgm-reviews.xml
import os, time, sys
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import re

API = "https://api.judge.me/api/v1/widgets/all_reviews_page"

SHOP_DOMAIN       = os.getenv("SHOP_DOMAIN", "").strip()            # *.myshopify.com
JDGM_PUBLIC_TOKEN = os.getenv("JDGM_PUBLIC_TOKEN", "").strip()
REVIEW_TYPE       = os.getenv("REVIEW_TYPE", "product-reviews").strip()  # product-reviews | shop-reviews | both
PAGE_START        = int(os.getenv("PAGE_START", "1"))
PAGE_LIMIT        = int(os.getenv("PAGE_LIMIT", "9999"))
PAGE_DELAY_MS     = float(os.getenv("PAGE_DELAY_MS", "200"))  # ms

def dbg(msg): print(f"[DEBUG] {msg}")

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
        return str(j.get("html") or j.get("widget") or j.get("data") or "")
    return r.text

def extract_reviews(html, dump_path=None):
    if dump_path:
        Path(dump_path).write_text(html, encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html or "", "lxml")
    return soup.select(".jdgm-rev")

def parse_review(node, rtype):
    def text(sels):
        for sel in sels:
            el = node.select_one(sel)
            if el and el.get_text(strip=True):
                return el.get_text(" ", strip=True)
        return ""
    rating = node.get("data-rating","")
    if not rating:
        aria = node.get("aria-label","")
        m = re.search(r"([1-5])\s*star", aria or "", re.I)
        rating = m.group(1) if m else ""
    prod   = node.select_one(".jdgm-rev__prod-link, .jdgm-rev__product")
    product_title = prod.get_text(" ", strip=True) if prod else ""
    link = node.select_one(".jdgm-rev__prod-link")
    product_url = link.get("href","") if link else ""

    return {
        "type": "shop" if rtype=="shop-reviews" else "product",
        "rating": rating or "",
        "title": text([".jdgm-rev__title"]),
        "body":  text([".jdgm-rev__body", ".jdgm-rev__content"]),
        "author":text([".jdgm-rev__author"]),
        "created_at": text([".jdgm-rev__timestamp", ".jdgm-rev__date"]),
        "product_title": product_title,
        "product_url": product_url,
        "photos": [img.get("src") for img in node.select("img") if img.get("src")]
    }

def crawl_one_type(rtype, dbg_dir):
    rows = []
    for p in range(PAGE_START, PAGE_LIMIT+1):
        html = fetch_page(p, rtype)
        dump = str(dbg_dir / f"raw_page{p}_{rtype}.html") if p == 1 else None
        nodes = extract_reviews(html, dump_path=dump)
        dbg(f"{rtype} page {p}: found {len(nodes)} .jdgm-rev")
        if not nodes: break
        rows += [parse_review(n, rtype) for n in nodes]
        time.sleep(PAGE_DELAY_MS/1000.0)
    return rows

def build_xml(rows):
    root = ET.Element("reviews", attrib={"generated_at": datetime.utcnow().isoformat(), "total": str(len(rows))})
    for r in rows:
        rev = ET.SubElement(root, "review", attrib={"type": r["type"], "rating": str(r["rating"])})
        for k in ["title","body","author","created_at","product_title","product_url"]:
            ET.SubElement(rev, k).text = r[k] or ""
        if r["photos"]:
            photos = ET.SubElement(rev, "photos")
            for u in r["photos"]: ET.SubElement(photos, "photo").text = u
    return ET.ElementTree(root)

def main():
    out = Path("dist"); out.mkdir(exist_ok=True)
    dbg_dir = out / "debug"; dbg_dir.mkdir(exist_ok=True)

    types = ["product-reviews","shop-reviews"] if REVIEW_TYPE=="both" else [REVIEW_TYPE]
    allrows = []
    for t in types: allrows += crawl_one_type(t, dbg_dir)

    tree = build_xml(allrows)
    outfile = out / "jdgm-reviews.xml"
    tree.write(outfile, encoding="utf-8", xml_declaration=True)
    print(f"✓ wrote {outfile} with {len(allrows)} reviews")

if __name__ == "__main__":
    main()
