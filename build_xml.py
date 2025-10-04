# build_xml.py — debug mạnh
import os, sys, time, re
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

API = "https://api.judge.me/api/v1/widgets/all_reviews_page"

SHOP_DOMAIN       = os.getenv("SHOP_DOMAIN", "").strip()
JDGM_PUBLIC_TOKEN = os.getenv("JDGM_PUBLIC_TOKEN", "").strip()
REVIEW_TYPE       = os.getenv("REVIEW_TYPE", "product-reviews").strip()
PAGE_START        = int(os.getenv("PAGE_START", "1"))
PAGE_LIMIT        = int(os.getenv("PAGE_LIMIT", "9999"))
PAGE_DELAY_MS     = float(os.getenv("PAGE_DELAY_MS", "200"))

def log(msg): print(f"[DEBUG] {msg}")

if not SHOP_DOMAIN or not JDGM_PUBLIC_TOKEN:
    print("Missing SHOP_DOMAIN or JDGM_PUBLIC_TOKEN")
    sys.exit(1)

def fetch_page(page, rtype):
    params = {
        "shop_domain": SHOP_DOMAIN,
        "api_token": JDGM_PUBLIC_TOKEN,
        "page": str(page),
        "review_type": rtype
    }
    r = requests.get(API, params=params, timeout=60)
    ct = (r.headers.get("content-type") or "").lower()
    url_no_token = r.url.replace(JDGM_PUBLIC_TOKEN, "*****")
    log(f"GET {url_no_token} -> {r.status_code} ({ct})")
    r.raise_for_status()
    if "application/json" in ct:
        j = r.json()
        body = (j.get("html") or j.get("widget") or j.get("data") or "")
        if not body:
            # nếu json không có html, ta lưu raw json cho bạn xem
            body = str(j)
        return body
    return r.text

def extract_nodes(html):
    soup = BeautifulSoup(html or "", "lxml")
    return soup.select(".jdgm-rev")

def parse_node(node, rtype):
    def txt(sels):
        for s in sels:
            el = node.select_one(s)
            if el and el.get_text(strip=True):
                return el.get_text(" ", strip=True)
        return ""
    rating = node.get("data-rating","")
    if not rating and node.has_attr("aria-label"):
        m = re.search(r"([1-5])\s*star", node["aria-label"], re.I)
        rating = m.group(1) if m else ""
    link = node.select_one(".jdgm-rev__prod-link")
    return {
        "type": "shop" if rtype=="shop-reviews" else "product",
        "rating": rating or "",
        "title": txt([".jdgm-rev__title"]),
        "body":  txt([".jdgm-rev__body",".jdgm-rev__content"]),
        "author":txt([".jdgm-rev__author"]),
        "created_at": txt([".jdgm-rev__timestamp",".jdgm-rev__date"]),
        "product_title": txt([".jdgm-rev__prod-link",".jdgm-rev__product"]),
        "product_url": (link.get("href") if link else ""),
        "photos": [img.get("src") for img in node.select("img") if img.get("src")]
    }

def build_xml(rows):
    root = ET.Element("reviews", attrib={"generated_at": datetime.utcnow().isoformat(), "total": str(len(rows))})
    for r in rows:
        rev = ET.SubElement(root, "review", attrib={"type": r["type"], "rating": str(r["rating"])})
        for k in ["title","body","author","created_at","product_title","product_url"]:
            ET.SubElement(rev, k).text = r[k] or ""
        if r["photos"]:
            photos = ET.SubElement(rev, "photos")
            for u in r["photos"]:
                ET.SubElement(photos, "photo").text = u
    return ET.ElementTree(root)

def crawl_type(rtype, out_dir):
    rows = []
    for p in range(PAGE_START, PAGE_LIMIT+1):
        body = fetch_page(p, rtype)
        if p == 1:
            # luôn ghi trang 1 để bạn mở xem
            (out_dir / f"raw_page1_{rtype}.body").write_text(body, encoding="utf-8", errors="ignore")
            log(f"Saved debug: {out_dir/f'raw_page1_{rtype}.body'} (len={len(body)})")
        nodes = extract_nodes(body)
        log(f"{rtype} page {p}: found {len(nodes)} .jdgm-rev")
        if not nodes:
            break
        rows += [parse_node(n, rtype) for n in nodes]
        time.sleep(PAGE_DELAY_MS/1000.0)
    return rows

def main():
    out = Path("dist"); out.mkdir(exist_ok=True)
    dbg = out / "debug"; dbg.mkdir(exist_ok=True)

    types = ["product-reviews","shop-reviews"] if REVIEW_TYPE=="both" else [REVIEW_TYPE]
    allrows = []
    for t in types:
        allrows += crawl_type(t, dbg)

    xml_path = out / "jdgm-reviews.xml"
    build_xml(allrows).write(xml_path, encoding="utf-8", xml_declaration=True)
    print(f"✓ wrote {xml_path} with {len(allrows)} reviews")

if __name__ == "__main__":
    main()
