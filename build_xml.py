# build_xml.py — JSON-first, fallback HTML
import os, sys, time, re, json
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

API = "https://api.judge.me/api/v1/widgets/all_reviews_page"

SHOP_DOMAIN       = os.getenv("SHOP_DOMAIN", "").strip()
JDGM_PUBLIC_TOKEN = os.getenv("JDGM_PUBLIC_TOKEN", "").strip()
REVIEW_TYPE       = os.getenv("REVIEW_TYPE", "product-reviews").strip()  # product-reviews | shop-reviews | both
PAGE_START        = int(os.getenv("PAGE_START", "1"))
PAGE_LIMIT        = int(os.getenv("PAGE_LIMIT", "9999"))
PAGE_DELAY_MS     = float(os.getenv("PAGE_DELAY_MS", "200"))

def log(msg): print(f"[DEBUG] {msg}")

if not SHOP_DOMAIN or not JDGM_PUBLIC_TOKEN:
    print("Missing SHOP_DOMAIN or JDGM_PUBLIC_TOKEN"); sys.exit(1)

def http_get(page, rtype):
    params = {
        "shop_domain": SHOP_DOMAIN,
        "api_token": JDGM_PUBLIC_TOKEN,
        "page": str(page),
        "review_type": rtype
    }
    r = requests.get(API, params=params, timeout=60)
    url_mask = r.url.replace(JDGM_PUBLIC_TOKEN, "*****")
    ct = (r.headers.get("content-type") or "").lower()
    log(f"GET {url_mask} -> {r.status_code} ({ct})")
    r.raise_for_status()
    return ct, r

# ---------- JSON PARSER ----------
# Nhiều shop trả JSON thay vì HTML. Ta cố gắng tìm list reviews trong JSON.
REVIEW_LIKE_KEYS = {"reviews", "all_reviews", "records", "items", "data"}

def flat_iter(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from flat_iter(v)
    elif isinstance(obj, list):
        for i in obj:
            yield from flat_iter(i)

def json_to_review_rows(j, rtype):
    # 1) Nếu có field "html" → trả về None để HTML parser xử lý
    if isinstance(j, dict) and any(k in j for k in ("html","widget")):
        html = j.get("html") or j.get("widget") or j.get("data")
        if isinstance(html, str) and "<" in html:
            return None  # HTML fallback

    # 2) Tìm mảng review trong JSON (dựa trên keys phổ biến)
    candidates = []
    if isinstance(j, dict):
        for k, v in flat_iter(j):
            if k in REVIEW_LIKE_KEYS and isinstance(v, list) and v:
                # check phần tử có mùi review
                sample = v[0]
                if isinstance(sample, (dict,)):
                    fields = set(map(str.lower, sample.keys()))
                    if {"body","rating"} & fields or {"title","rating"} & fields or "review" in "".join(fields):
                        candidates.append(v)

    if not candidates:
        return []  # JSON nhưng không có list review rõ ràng

    rows = []
    for arr in candidates:
        for r in arr:
            if not isinstance(r, dict): continue
            # Thử map các field phổ biến
            def pick(*names):
                for n in names:
                    if n in r and isinstance(r[n], str): return r[n]
                    # nested
                    parts = n.split(".")
                    cur = r
                    ok = True
                    for p in parts:
                        if isinstance(cur, dict) and p in cur:
                            cur = cur[p]
                        else:
                            ok = False; break
                    if ok and isinstance(cur, str): return cur
                return ""
            # rating có thể là số
            rating = r.get("rating") or r.get("score") or r.get("stars") or r.get("rating_value")
            try: rating = str(int(float(rating)))
            except: rating = str(rating or "")
            row = {
                "type": "shop" if REVIEW_TYPE=="shop-reviews" else ("shop" if r.get("review_type")=="shop" else "product"),
                "rating": rating,
                "title": pick("title","review_title"),
                "body": pick("body","content","review_body","text"),
                "author": pick("author","reviewer.name","reviewer","customer_name"),
                "created_at": pick("created_at","date","submitted_at","created"),
                "product_title": pick("product_title","product.title","product_name"),
                "product_url": pick("product_url","product.url","product_handle"),
                "photos": []
            }
            # ảnh
            imgs = r.get("photos") or r.get("images") or []
            if isinstance(imgs, list):
                for it in imgs:
                    if isinstance(it, str):
                        row["photos"].append(it)
                    elif isinstance(it, dict):
                        url = it.get("url") or it.get("src")
                        if url: row["photos"].append(url)
            rows.append(row)
    return rows

# ---------- HTML PARSER ----------
def html_to_nodes(html):
    soup = BeautifulSoup(html or "", "lxml")
    return soup.select(".jdgm-rev") or soup.select("[class*='jdgm'][class*='rev']")

def node_to_row(node, rtype):
    def txt(sels):
        for sel in sels:
            el = node.select_one(sel)
            if el and el.get_text(strip=True):
                return el.get_text(" ", strip=True)
        return ""
    rating = node.get("data-rating","")
    if not rating and node.has_attr("aria-label"):
        m = re.search(r"([1-5])\s*star", node["aria-label"], re.I)
        rating = m.group(1) if m else ""
    link = node.select_one(".jdgm-rev__prod-link")
    prod = node.select_one(".jdgm-rev__prod-link, .jdgm-rev__product")
    return {
        "type": "shop" if rtype=="shop-reviews" else "product",
        "rating": rating or "",
        "title": txt([".jdgm-rev__title"]),
        "body":  txt([".jdgm-rev__body", ".jdgm-rev__content"]),
        "author":txt([".jdgm-rev__author"]),
        "created_at": txt([".jdgm-rev__timestamp", ".jdgm-rev__date"]),
        "product_title": prod.get_text(" ", strip=True) if prod else "",
        "product_url": (link.get("href") if link else ""),
        "photos": [img.get("src") for img in node.select("img") if img.get("src")]
    }

def build_xml(rows):
    root = ET.Element("reviews", attrib={
        "generated_at": datetime.utcnow().isoformat(),
        "total": str(len(rows))
    })
    for r in rows:
        rev = ET.SubElement(root, "review", attrib={"type": r["type"], "rating": str(r["rating"])})
        for k in ["title","body","author","created_at","product_title","product_url"]:
            ET.SubElement(rev, k).text = r.get(k,"") or ""
        if r.get("photos"):
            photos = ET.SubElement(rev, "photos")
            for u in r["photos"]:
                if u: ET.SubElement(photos, "photo").text = u
    return ET.ElementTree(root)

def crawl_type(rtype, out_dir):
    allrows = []
    for page in range(PAGE_START, PAGE_LIMIT+1):
        ct, resp = http_get(page, rtype)
        body_dump = out_dir / f"raw_page1_{rtype}.body"
        if page == 1:
            # lưu body thô để dễ debug
            text_preview = resp.text if "json" not in ct else json.dumps(resp.json())[:2000]
            body_dump.write_text(text_preview, encoding="utf-8", errors="ignore")
            log(f"Saved debug: {body_dump} (len={len(text_preview)})")

        if "json" in ct:
            data = resp.json()
            rows = json_to_review_rows(data, rtype)
            if rows is None:
                # JSON nhưng chứa HTML trong 'html'
                html = data.get("html") or data.get("widget") or data.get("data") or ""
                nodes = html_to_nodes(html)
                rows = [node_to_row(n, rtype) for n in nodes]
            log(f"{rtype} page {page}: JSON rows={len(rows)}")
            if not rows: break
            allrows += rows
        else:
            html = resp.text
            nodes = html_to_nodes(html)
            log(f"{rtype} page {page}: HTML nodes={len(nodes)}")
            if not nodes: break
            allrows += [node_to_row(n, rtype) for n in nodes]

        time.sleep(PAGE_DELAY_MS/1000.0)
    return allrows

def main():
    out = Path("dist"); out.mkdir(exist_ok=True)
    dbg = out / "debug"; dbg.mkdir(exist_ok=True)

    types = ["product-reviews","shop-reviews"] if REVIEW_TYPE=="both" else [REVIEW_TYPE]
    allrows = []
    for t in types:
        allrows += crawl_type(t, dbg)

    tree = build_xml(allrows)
    xml_path = out / "jdgm-reviews.xml"
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    print(f"✓ wrote {xml_path} with {len(allrows)} reviews")

if __name__ == "__main__":
    main()
