#!/usr/bin/env python3
"""문서 세트를 하나의 자립형 페이지로 합칩니다 (웹 배포용).

로컬 폴더에서는 docs/*.html 을 그대로 쓰면 되지만, 한 장으로 공유할 때는
문서 간 링크가 끊어지므로 전부 인페이지 앵커로 바꿔 붙입니다.

    python3 build/combine.py   →  out/storyai-설계-통합.html
"""
import re, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "out" / "docs"
CSS = (ROOT / "build" / "style.css").read_text(encoding="utf-8")
MOCKUP_URL = "https://claude.ai/code/artifact/4b116cae-a32c-4f8b-9ba5-7ae1ad53805a"

DOCS = [
    ("index.html",        "00", "문서 허브",   "전체 지도와 핵심 결정"),
    ("01-기획서.html",     "01", "기획서",      "왜 만드는가, 무엇이 다른가"),
    ("02-설계서.html",     "02", "설계서",      "데이터 모델과 MCP 명세"),
    ("03-개발계획서.html",  "03", "개발계획서",  "로드맵과 태스크 분해"),
    ("04-기술스택.html",   "04", "기술 스택",   "선택과 거부의 근거"),
    ("05-구조도.html",     "05", "구조도",      "다이어그램 전집"),
    ("06-UI설계.html",     "06", "UI 설계",     "화면 명세와 디자인 토큰"),
]
FILE2NUM = {f: n for f, n, _, _ in DOCS}

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Gowun+Batang:wght@400;700&family=IBM+Plex+Mono:wght@400;500;600'
         '&family=IBM+Plex+Sans+KR:wght@300;400;500;600;700&display=swap">')


def extract_main(html):
    m = re.search(r'<main class="doc">(.*)</main>', html, re.S)
    body = m.group(1)
    body = re.sub(r'<div class="trail">.*?</div>\s*$', "", body, flags=re.S)
    return body


def prefix_ids(body, num):
    body = re.sub(r'\bid="([^"]+)"', lambda m: f'id="d{num}-{m.group(1)}"', body)
    # SVG 내부 참조도 함께
    body = re.sub(r'url\(#([^)]+)\)', lambda m: f'url(#d{num}-{m.group(1)})', body)
    body = re.sub(r'\bhref="#([^"]+)"',
                  lambda m: f'href="#d{num}-{m.group(1)}"', body)
    return body


def rewrite_links(body):
    def repl(m):
        f, frag = m.group(1), m.group(2)
        if f == "07-UI목업.html":
            return f'href="{MOCKUP_URL}" target="_blank" rel="noopener"'
        n = FILE2NUM.get(f)
        if not n:
            return m.group(0)
        return f'href="#d{n}-{frag}"' if frag else f'href="#doc-{n}"'
    return re.sub(r'href="([^":#]+\.html)(?:#([^"]*))?"', repl, body)


def main():
    parts, rail = [], []
    for fn, num, title, sub in DOCS:
        html = (D / fn).read_text(encoding="utf-8")
        body = prefix_ids(extract_main(html), num)
        body = rewrite_links(body)
        parts.append(
            f'<section class="docsec" id="doc-{num}">\n'
            f'<div class="secmark"><span class="n">{num}</span>{title}</div>\n{body}\n</section>')
        rail.append(f'<a class="d" href="#doc-{num}">'
                    f'<span class="n">{num}</span><span>{title}</span></a>')
    rail.append(f'<a class="d" href="{MOCKUP_URL}" target="_blank" rel="noopener">'
                f'<span class="n">07</span><span>UI 목업 ↗</span></a>')

    extra = """
.docsec{border-top:1px solid var(--line);padding-top:8px;margin-top:70px}
.docsec:first-of-type{border-top:none;margin-top:0;padding-top:0}
.secmark{font-family:var(--mono);font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;
  color:var(--thread);font-weight:600;margin-bottom:26px;display:flex;gap:9px;align-items:center}
.secmark .n{background:var(--thread-bg);padding:2px 7px;border-radius:4px}
.docsec .mast{margin-bottom:38px}
"""
    page = f"""<title>storyai 설계 문서</title>
{FONTS}
<style>
{CSS}{extra}</style>
<div class="wrap">
<nav class="rail">
<a class="mk" href="#doc-00">
<svg width="20" height="24" viewBox="0 0 20 24" fill="none" aria-hidden="true">
<path d="M3 2v20M17 2v20" stroke="currentColor" stroke-width="1.2" opacity=".3"/>
<path d="M3 7c5 0 5 5 7 5s2-5 7-5" stroke="var(--thread)" stroke-width="1.6" fill="none"/>
<path d="M3 15c5 0 5 5 7 5s2-5 7-5" stroke="var(--thread)" stroke-width="1.6" fill="none" opacity=".45"/>
</svg><b>storyai</b></a>
<p class="sub">설계 문서 통합본 v1</p>
<h4>문서</h4>
{chr(10).join(rail)}
</nav>
<main class="doc">
{chr(10).join(parts)}
</main>
</div>
"""
    out = ROOT / "out" / "storyai-설계-통합.html"
    out.write_text(page, encoding="utf-8")
    print(f"combined → {out}  ({len(page)//1024} KB)")

    # 자체 점검
    ids = set(re.findall(r'id="([^"]+)"', page))
    bad = [f for f in set(re.findall(r'href="#([^"]+)"', page)) if f not in ids]
    print("깨진 앵커:", bad if bad else "없음")
    for t in ("figure", "table", "div", "pre", "svg", "section", "main", "nav"):
        o = len(re.findall(r"<" + t + r"[ >]", page)); c = page.count("</" + t + ">")
        if o != c:
            print(f"  ⚠ <{t}> {o}/{c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
