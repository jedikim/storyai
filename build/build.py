#!/usr/bin/env python3
"""storyai 문서 세트 빌더.

build/parts/*.part.html 의 본문 조각을 읽어 out/docs/*.html 로 조립합니다.
공통 스타일은 인라인되므로 각 문서는 자립형입니다 — 로컬에서 열어도,
어디에 올려도 그대로 렌더됩니다.

    python3 build/build.py
"""
import re, sys, pathlib, html

ROOT = pathlib.Path(__file__).resolve().parent.parent
PARTS = ROOT / "build" / "parts"
OUT = ROOT / "out" / "docs"
CSS = (ROOT / "build" / "style.css").read_text(encoding="utf-8")

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Gowun+Batang:wght@400;700&family=IBM+Plex+Mono:wght@400;500;600'
         '&family=IBM+Plex+Sans+KR:wght@300;400;500;600;700&display=swap">')

# (파일명, 번호, 제목, 부제, 대상 독자)
DOCS = [
    ("index.html",        "",   "문서 허브",     "전체 지도와 핵심 결정",            "모두"),
    ("01-기획서.html",     "01", "기획서",        "왜 만드는가, 무엇이 다른가",        "기획자"),
    ("02-설계서.html",     "02", "설계서",        "데이터 모델과 MCP 명세",           "개발자"),
    ("03-개발계획서.html",  "03", "개발계획서",    "로드맵과 태스크 분해",             "개발자"),
    ("04-기술스택.html",   "04", "기술 스택",      "선택과 거부의 근거",               "개발자"),
    ("05-구조도.html",     "05", "구조도",        "다이어그램 전집",                  "모두"),
    ("06-UI설계.html",     "06", "UI 설계",       "화면 명세와 디자인 토큰",           "디자이너·개발자"),
    ("07-UI목업.html",     "07", "UI 목업",       "실제로 동작하는 화면",             "모두"),
]

MARK = ('<a class="mk" href="index.html">'
        '<svg width="20" height="24" viewBox="0 0 20 24" fill="none" aria-hidden="true">'
        '<path d="M3 2v20M17 2v20" stroke="currentColor" stroke-width="1.2" opacity=".3"/>'
        '<path d="M3 7c5 0 5 5 7 5s2-5 7-5" stroke="var(--thread)" stroke-width="1.6" fill="none"/>'
        '<path d="M3 15c5 0 5 5 7 5s2-5 7-5" stroke="var(--thread)" stroke-width="1.6" fill="none" opacity=".45"/>'
        '</svg><b>storyai</b></a>\n<p class="sub">설계 문서 세트 v1</p>')


def doc_nav(current):
    rows = []
    for fn, num, title, sub, who in DOCS:
        cur = " cur" if fn == current else ""
        n = f'<span class="n">{num}</span>' if num else '<span class="n">·</span>'
        rows.append(f'<a class="d{cur}" href="{fn}">{n}<span>{title}</span></a>')
    return "\n".join(rows)


def page_toc(body):
    """본문의 h2/h3에서 목차를 뽑습니다."""
    out = []
    for m in re.finditer(
            r'<h([23]) id="([^"]+)">(?:<span class="num">([^<]*)</span>)?([^<]*)', body):
        lvl, hid, num, txt = m.groups()
        num = (num or "").strip()
        txt = (txt or "").strip()
        if not txt:
            continue
        pad = "" if lvl == "2" else ' style="padding-left:24px;font-size:11.5px"'
        n = f'<span class="n">{html.escape(num.split("/")[0].strip())}</span>' if num else ""
        out.append(f'<a href="#{hid}"{pad}>{n}{html.escape(txt)}</a>')
    return "\n".join(out)


def trail(current):
    idx = [d[0] for d in DOCS].index(current)
    items = []
    if idx > 0:
        f, n, t, s, w = DOCS[idx - 1]
        items.append(f'<a href="{f}"><div class="k">← 이전</div><div class="t">{t}</div></a>')
    if idx < len(DOCS) - 1:
        f, n, t, s, w = DOCS[idx + 1]
        items.append(f'<a href="{f}"><div class="k">다음 →</div><div class="t">{t}</div></a>')
    return f'<div class="trail">{"".join(items)}</div>' if items else ""


def build_one(fn, num, title, sub, who):
    part = PARTS / (fn.replace(".html", ".part.html"))
    if not part.exists():
        print(f"  skip (no part): {fn}")
        return None
    body = part.read_text(encoding="utf-8")
    toc = page_toc(body)
    toc_block = f'<div class="toc"><h4>이 문서</h4>{toc}</div>' if toc else ""
    eyebrow = f'storyai · {num + " " if num else ""}{title} · 2026-08-24'
    page = f"""<title>{title} — storyai</title>
{FONTS}
<style>
{CSS}</style>
<div class="wrap">
<nav class="rail">
{MARK}
<h4>문서</h4>
{doc_nav(fn)}
{toc_block}
</nav>
<main class="doc">
<header class="mast">
  <div class="eyebrow">{eyebrow}</div>
  {body.split("<!--BODY-->")[0]}
</header>
{body.split("<!--BODY-->")[1] if "<!--BODY-->" in body else body}
{trail(fn)}
</main>
</div>
"""
    (OUT / fn).write_text(page, encoding="utf-8")
    return fn


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    built = []
    for d in DOCS:
        r = build_one(*d)
        if r:
            built.append(r)
    print(f"built {len(built)} docs -> {OUT}")
    # 자체 점검
    bad = 0
    for fn in built:
        s = (OUT / fn).read_text(encoding="utf-8")
        for t in ("figure", "table", "div", "pre", "svg", "main", "nav"):
            o = len(re.findall(r"<" + t + r"[ >]", s))
            c = s.count("</" + t + ">")
            if o != c:
                print(f"  ⚠ {fn}: <{t}> {o} open / {c} close")
                bad += 1
    print("tag balance:", "OK" if not bad else f"{bad} issue(s)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
