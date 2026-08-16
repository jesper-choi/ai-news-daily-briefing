"""표현 계층. 페이지 HTML과 HTTP 핸들러."""
import html
import re
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler

from .repository import available_dates, load_cache_for_date
from .service import ensure_today_cache_started, is_generating


def _text_paragraphs(text):
    parts = [html.escape(p.strip()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{p}</p>" for p in parts)

# d2svg는 생성 시점에 이미 컴파일된 SVG(diagrams.bake_diagrams), mermaid는 d2로 넘어가기
# 전에 만들어진 옛 날짜 캐시용. 옛 캐시를 다시 만들 수는 없으니 그쪽 경로는 남겨둔다.
_BLOCK = re.compile(r"```(d2svg|mermaid)[ \t]*\n(.*?)```", re.S)


def paragraphs_html(text):
    """빈 줄로 구분된 텍스트를 <p>로, 코드블록은 다이어그램으로 변환.
    본문은 escape하지만 d2svg 블록만은 이미 우리가 만든 SVG라 그대로 심는다."""
    out, pos = [], 0
    for m in _BLOCK.finditer(text):
        out.append(_text_paragraphs(text[pos:m.start()]))
        kind, body = m.group(1), m.group(2).strip()
        if body and kind == "d2svg":
            out.append(f'<div class="diagram">{body}</div>')
        elif body:  # 옛 mermaid 캐시: 원문을 심어두고 브라우저에서 그린다
            out.append(f'<div class="diagram"><pre class="mermaid">{html.escape(body)}</pre></div>')
        pos = m.end()
    out.append(_text_paragraphs(text[pos:]))
    return "".join(out) or f"<p>{html.escape(text)}</p>"

# 별도 상수로 빼둔 이유: 페이지 전체가 f-string이라 여기 중괄호를 전부 이중으로 써야 함.
# 다이어그램이 있는 페이지에서만 삽입되므로 없는 날은 CDN을 받지도 않는다.
MERMAID_SCRIPT = """
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  // 색은 페이지 CSS 변수에서 그대로 가져온다 -> 라이트/다크 팔레트를 여기 또 적어둘
  // 필요가 없고, 본문 색과 항상 같이 움직임.
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  mermaid.initialize({
    startOnLoad: false, securityLevel: "strict",
    theme: "base",
    // useMaxWidth: false 가 핵심. 기본값(true)이면 mermaid가 svg를 컨테이너 폭에
    // 맞춰 축소해서, 넓은 다이어그램일수록 글씨가 읽을 수 없게 작아짐. 원래 크기로
    // 그리게 두고 넘치면 .diagram 쪽에서 가로 스크롤로 처리한다.
    flowchart: { useMaxWidth: false, curve: "basis", nodeSpacing: 45, rankSpacing: 55, padding: 14 },
    sequence: { useMaxWidth: false },
    themeVariables: {
      fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
      fontSize: "15px",
      primaryColor: v("--accent-soft"), primaryBorderColor: v("--accent"),
      primaryTextColor: v("--text"), mainBkg: v("--accent-soft"), nodeBorder: v("--accent"),
      secondaryColor: v("--card"), tertiaryColor: v("--bg"),
      lineColor: v("--muted"), textColor: v("--text"),
      clusterBkg: v("--bg"), clusterBorder: v("--border"),
      edgeLabelBackground: v("--card"),
      actorBkg: v("--accent-soft"), actorBorder: v("--accent"), actorTextColor: v("--text"),
      signalColor: v("--muted"), signalTextColor: v("--text"),
      noteBkgColor: v("--card"), noteTextColor: v("--text"), noteBorderColor: v("--border"),
      labelBoxBkgColor: v("--card"), labelTextColor: v("--text"),
    },
  });
  // LLM이 만든 다이어그램은 문법이 깨질 때가 있음 -> 하나씩 그리고, 실패한 것만 조용히
  // 접어서 숨긴다(요약 본문은 그대로 읽을 수 있게). 페이지 전체가 죽으면 안 됨.
  async function draw(root) {
    for (const node of root.querySelectorAll("pre.mermaid:not([data-done])")) {
      node.dataset.done = "1";
      try {
        const { svg } = await mermaid.render("d" + Math.random().toString(36).slice(2), node.textContent);
        node.innerHTML = svg;
      } catch (e) {
        node.closest(".diagram")?.remove();
      }
    }
  }
  // <details>가 닫혀 있으면 폭이 0이라 그래프가 찌그러짐 -> 펼칠 때 처음 한 번만 그린다.
  for (const d of document.querySelectorAll("details.detail-toggle")) {
    d.addEventListener("toggle", () => { if (d.open) draw(d); });
  }
</script>"""

def date_picker_html(selected, available):
    # available은 '캐시 파일이 실제로 있는 날짜'라서 생성 중인 오늘자는 빠져 있음.
    # 오늘자는 항상 목록에 넣어야 함 - 예전엔 selected일 때만 합성해서 넣는 바람에,
    # 생성 중에 과거 날짜를 고르면 오늘자 옵션이 사라져서 돌아올 방법이 없었음.
    # selected도 같이 넣어줌(캐시 없는 날짜를 URL로 직접 연 경우 선택 상태 유지).
    today = date.today().isoformat()
    dates = sorted({today, selected, *available}, reverse=True)
    options = "".join(
        f'<option value="{d}"{" selected" if d == selected else ""}>'
        f'{d}{" (오늘)" if d == today else ""}'
        f'{("" if d in available else (" · 생성 중" if d == today else " · 없음"))}</option>'
        for d in dates
    )
    return f"""<select class="date-picker" onchange="location.href='/?date='+this.value">{options}</select>"""

def render_html(day_str, available, data, generating=False, regenerating=False):
    refresh_tag = '<meta http-equiv="refresh" content="6">' if generating else ""
    if generating:
        title = "다시 생성하고 있어요…" if regenerating else "오늘의 브리핑을 만들고 있어요…"
        body = f"""
        <div class="generating">
          <div class="pulse-dots"><span></span><span></span><span></span></div>
          <p>{title}</p>
          <p class="hint">GeekNews · Hacker News를 훑어서 AI 관련 기사를 고르고, 20개 각각 상세 요약까지 만들고 있어요.<br>보통 5~10분 걸려요. 이 페이지는 6초마다 자동으로 새로고침돼요.</p>
        </div>"""
        item_count, generated_at = 0, ""
    elif data is None:
        body = f"""<p class="empty">{html.escape(day_str)} 요약이 없습니다. 그날은 생성되지 않았어요.</p>"""
        item_count, generated_at = 0, ""
    else:
        sections_html = []
        for section in data["sections"]:
            cards = []
            for it in section["items"]:
                title = html.escape(it["title"])
                domain = html.escape(it["domain"])
                abstract = html.escape(it["abstract"])
                link = html.escape(it["link"])
                discuss_url = html.escape(it["discuss_url"])
                detail_html = paragraphs_html(it["detail"])
                # 본문을 못 가져와 짧은 소개글만으로 요약한 경우 -> 읽는 사람이 요약의
                # 근거가 얇다는 걸 알 수 있게 표시 (봇 차단/로그인벽/유튜브 링크 등)
                # 요약이 원문 본문 기반인지 아닌지를 밝혀둔다(무엇을 읽고 쓴 요약인지)
                badges = {
                    "topic": ("원문을 가져오지 못해 GeekNews 토픽 페이지의 요약을 "
                              "바탕으로 정리했어요", "긱뉴스 요약 기반"),
                    "listing": ("원문 본문을 가져오지 못해 목록의 짧은 소개글만으로 "
                                "요약했어요", "소개글 기반"),
                }.get(it.get("summary_source"))
                thin_badge = (f'<span class="badge-thin" title="{badges[0]}">{badges[1]}</span>'
                              if badges else "")
                # 소스마다 있는 정보가 달라서(뉴스레터엔 추천수/토론 스레드가 없고 대신
                # 발행일이 있음) 있는 항목만 골라 · 로 이어붙인다
                meta_bits = [f"<span>{domain}</span>"]
                if it.get("published"):
                    meta_bits.append(f"<span>{html.escape(it['published'])}</span>")
                if it.get("points"):
                    meta_bits.append(f"<span>▲ {html.escape(str(it['points']))}</span>")
                if it.get("discuss_url"):
                    meta_bits.append(f'<a class="meta-link" href="{discuss_url}" '
                                     f'target="_blank" rel="noopener">토론 보기</a>')
                if thin_badge:
                    meta_bits.append(thin_badge)
                meta_html = '<span class="dot">·</span>'.join(meta_bits)
                cards.append(f"""
        <article class="entry">
          <p class="index">{it['rank']:02d}</p>
          <h3>{title}</h3>
          <div class="meta">{meta_html}</div>
          <p class="abstract">{abstract}</p>
          <details class="detail-toggle">
            <summary>전체 요약 읽기</summary>
            <div class="detail-text">{detail_html}</div>
            <button type="button" class="detail-close" onclick="var d=this.closest('details');d.open=false;d.scrollIntoView({{block:'nearest'}});">↑ 접기</button>
          </details>
          <div class="actions">
            <a class="btn-primary" href="{link}" target="_blank" rel="noopener">원문 보기</a>
          </div>
        </article>""")
            if cards:
                sections_html.append(f"""
        <section class="source-section">
          <h2 class="source-label">{html.escape(section['label'])}</h2>
          {''.join(cards)}
        </section>""")
        body = "".join(sections_html)
        item_count = sum(len(s["items"]) for s in data["sections"])
        generated_at = data["generated_at"][11:16]

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Daily Briefing · {day_str}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI2NCIgaGVpZ2h0PSI2NCIgdmlld0JveD0iMCAwIDY0IDY0Ij4KPHJlY3QgeD0iMyIgeT0iMyIgd2lkdGg9IjU4IiBoZWlnaHQ9IjU4IiByeD0iMTQiIGZpbGw9IiNmZmZkZjgiIHN0cm9rZT0iI2U2ZTBkMiIgc3Ryb2tlLXdpZHRoPSIyIi8+Cjx0ZXh0IHg9IjI3IiB5PSI0NiIgZm9udC1mYW1pbHk9Ikdlb3JnaWEsICdJb3dhbiBPbGQgU3R5bGUnLCAnUGFsYXRpbm8gTGlub3R5cGUnLCBzZXJpZiIgZm9udC1zdHlsZT0iaXRhbGljIiBmb250LXdlaWdodD0iNzAwIiBmb250LXNpemU9IjQwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSIjMmY1ZDhhIj5BPC90ZXh0Pgo8Y2lyY2xlIGN4PSI0NSIgY3k9IjE2IiByPSI1IiBmaWxsPSIjZTA3OTNhIi8+Cjwvc3ZnPgo=">
{refresh_tag}
<style>
  :root {{
    --bg: #f7f4ed; --card: #fffdf8; --text: #2b2820; --muted: #837a68;
    --border: #e6e0d2; --accent: #2f5d8a; --accent-soft: #dde7f0;
    /* 본문 폭. 다이어그램이 들어가면서 700px로는 너무 좁아 축소/스크롤이 잦았음 */
    --page: 1180px; --page-pad: 1.25rem;
    --font-serif: Georgia, "Iowan Old Style", "Palatino Linotype", "Noto Serif KR", serif;
    --font-sans: -apple-system, BlinkMacSystemFont, "Pretendard", "Apple SD Gothic Neo", "Segoe UI", sans-serif;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #191611; --card: #201c15; --text: #ece6d8; --muted: #a89d86;
      --border: #363024; --accent: #7fb0e0; --accent-soft: #1e2c3a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: var(--font-sans); line-height: 1.7; -webkit-font-smoothing: antialiased;
  }}
  header {{
    max-width: var(--page); margin: 0 auto; padding: 3.5rem var(--page-pad) 2.5rem;
    border-bottom: 1px solid var(--border);
  }}
  .eyebrow-row {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; }}
  .header-controls {{ display: flex; align-items: center; gap: .5rem; }}
  .btn-regenerate {{
    font-family: var(--font-sans); font-size: .8rem; color: var(--text); background: var(--card);
    border: 1px solid var(--border); border-radius: 8px; padding: .4rem .6rem; cursor: pointer;
    text-decoration: none; white-space: nowrap;
  }}
  .btn-regenerate:hover {{ border-color: var(--accent); color: var(--accent); }}
  .eyebrow {{
    display: block; font-size: .75rem; font-weight: 700; letter-spacing: .14em;
    text-transform: uppercase; color: var(--accent); margin-bottom: .9rem;
  }}
  .date-picker {{
    font-family: var(--font-sans); font-size: .8rem; color: var(--text); background: var(--card);
    border: 1px solid var(--border); border-radius: 8px; padding: .4rem .6rem; cursor: pointer;
  }}
  header h1 {{
    font-family: var(--font-serif); font-style: italic; font-weight: 500;
    font-size: clamp(2rem, 5vw, 2.75rem); margin: 0 0 .8rem; letter-spacing: -.01em; color: var(--text);
  }}
  header p {{ margin: 0; color: var(--muted); font-size: .92rem; }}
  main {{ max-width: var(--page); margin: 0 auto; padding: .5rem var(--page-pad) 6rem; }}
  .empty {{ padding: 4rem 0; text-align: center; color: var(--muted); font-size: 1rem; }}
  .generating {{ padding: 5rem 0; text-align: center; }}
  .pulse-dots {{ display: flex; justify-content: center; gap: .5rem; margin-bottom: 1.6rem; }}
  .pulse-dots span {{
    width: .65rem; height: .65rem; border-radius: 50%; background: var(--accent);
    animation: pulse 1.2s ease-in-out infinite;
  }}
  .pulse-dots span:nth-child(2) {{ animation-delay: .2s; }}
  .pulse-dots span:nth-child(3) {{ animation-delay: .4s; }}
  @keyframes pulse {{
    0%, 80%, 100% {{ opacity: .25; transform: scale(.7); }}
    40% {{ opacity: 1; transform: scale(1); }}
  }}
  .generating p {{ margin: 0 0 .6rem; font-size: 1.05rem; color: var(--text); }}
  .generating .hint {{ font-size: .85rem; color: var(--muted); line-height: 1.6; }}
  .source-section + .source-section {{ margin-top: 1rem; }}
  .source-label {{
    font-family: var(--font-sans); font-size: .78rem; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: var(--muted); margin: 0; padding: 1.5rem 0 .5rem;
    border-top: 2px solid var(--accent);
  }}
  .source-section:first-child .source-label {{ border-top: none; padding-top: 0; }}
  .entry {{ padding: 3rem 0; border-bottom: 1px solid var(--border); }}
  .entry:last-child {{ border-bottom: none; }}
  .index {{
    font-family: var(--font-serif); font-size: .95rem; font-weight: 700; color: var(--accent);
    margin: 0 0 .7rem; letter-spacing: .04em;
  }}
  .entry h3 {{
    font-family: var(--font-serif); font-weight: 500; font-size: 1.55rem; line-height: 1.4;
    margin: 0 0 .8rem; color: var(--text);
  }}
  .meta {{
    display: flex; align-items: center; gap: .5rem; margin-bottom: 1.3rem; flex-wrap: wrap;
    font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted);
  }}
  .meta .dot {{ opacity: .5; }}
  .badge-thin {{
    padding: .1rem .45rem; border-radius: 4px; border: 1px solid var(--border);
    background: var(--card); color: var(--muted); cursor: help;
    font-size: .72rem; letter-spacing: .03em; text-transform: none;
  }}
  .meta-link {{ color: var(--muted); text-decoration: none; border-bottom: 1px solid var(--border); }}
  .meta-link:hover {{ color: var(--accent); border-color: var(--accent); }}
  .abstract {{ margin: 0 0 1.1rem; font-size: 1.05rem; line-height: 1.8; color: var(--text); }}
  .detail-toggle {{ margin-bottom: 1.4rem; }}
  .detail-toggle summary {{
    cursor: pointer; user-select: none; font-size: .85rem; font-weight: 700; color: var(--accent);
    list-style: none; display: inline-flex; align-items: center; gap: .35rem;
    letter-spacing: .03em; text-transform: uppercase;
  }}
  .detail-toggle summary::-webkit-details-marker {{ display: none; }}
  .detail-toggle summary::after {{ content: "→"; transition: transform .15s ease; }}
  .detail-toggle[open] summary::after {{ transform: rotate(90deg); }}
  .detail-text {{
    margin-top: 1.3rem; padding: 0 0 0 1.3rem; border-left: 3px solid var(--accent-soft);
    font-size: 1rem; line-height: 1.85; color: var(--text);
  }}
  .detail-text p {{ margin: 0 0 1.15rem; }}
  .detail-text p:last-child {{ margin-bottom: 0; }}
  /* d2 그림은 자기 캔버스(회색/검정 판)를 갖고 나온다 -> 여기서 배경·테두리를 또
     주면 판이 이중으로 겹친다. 모서리만 둥글게 깎고 나머지는 SVG에 맡긴다. */
  .diagram {{
    margin: 1.4rem 0; border-radius: 14px; overflow: hidden;
    overflow-x: auto;  /* 넓은 다이어그램이 본문을 밀어내지 않게 */
  }}
  /* 옛 mermaid 캐시는 캔버스가 없으니 예전처럼 카드 배경을 준다 */
  .diagram:has(pre.mermaid) {{ padding: 1.1rem; background: var(--card); border: 1px solid var(--border); }}
  .diagram pre.mermaid {{ margin: 0; text-align: center; font-family: var(--font-sans); }}
  /* max-width를 풀어야 넓은 다이어그램이 축소되지 않고 원래 크기로 그려진다
     (넘치는 만큼은 .diagram의 overflow-x로 스크롤). 좁은 건 auto 마진으로 가운데. */
  .diagram svg {{ max-width: none; height: auto; display: block; margin: 0 auto; }}
  /* 라이트/다크용 SVG가 한 쌍으로 들어있고 여기서 하나만 보여준다 */
  .d2-dark {{ display: none; }}
  .d2-light {{ display: block; }}
  @media (prefers-color-scheme: dark) {{
    .d2-light {{ display: none; }}
    .d2-dark {{ display: block; }}
  }}
  .detail-close {{
    margin-top: 1.1rem; padding: .45rem 1rem; border-radius: 8px;
    border: 1px solid var(--border); background: var(--card); color: var(--muted);
    font-family: var(--font-sans); font-size: .8rem; font-weight: 700; cursor: pointer;
    letter-spacing: .03em;
  }}
  .detail-close:hover {{ border-color: var(--accent); color: var(--accent); }}
  .actions {{ display: flex; }}
  .btn-primary {{
    display: inline-flex; align-items: center; gap: .4rem; padding: .6rem 1.3rem;
    border-radius: 8px; background: var(--accent); color: #fff9f2; text-decoration: none;
    font-size: .85rem; font-weight: 700; transition: opacity .15s ease;
  }}
  .btn-primary:hover {{ opacity: .85; }}
  footer {{
    max-width: var(--page); margin: 0 auto; padding: 0 var(--page-pad) 4rem; text-align: center;
    color: var(--muted); font-size: .8rem;
  }}
</style>
</head>
<body>
  <header>
    <div class="eyebrow-row">
      <span class="eyebrow">Daily Briefing</span>
      <div class="header-controls">
        {date_picker_html(day_str, available)}
        {f'<a class="btn-regenerate" href="/?date={day_str}&regenerate=1">↻ 다시 생성</a>' if day_str == date.today().isoformat() and not generating else ""}
      </div>
    </div>
    <h1>AI Daily Briefing</h1>
    <p>{day_str}{f' · 총 {item_count}개 · {generated_at} 생성' if data else ''}</p>
  </header>
  <main>{body}</main>
  <footer>GeekNews · Hacker News 기반 · Gemini로 생성한 원문 요약</footer>
  {MERMAID_SCRIPT if 'class="mermaid"' in body else ''}
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return

        query = urllib.parse.parse_qs(parsed.query)
        requested = query.get("date", [None])[0]
        today_str = date.today().isoformat()
        # validate format so a bad ?date= can't be used for path traversal into cache_path()
        day_str = requested if requested and re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested) else today_str

        if query.get("regenerate", [None])[0] and day_str == today_str:
            ensure_today_cache_started(force=True)
            self.send_response(302)
            self.send_header("Location", f"/?date={today_str}")
            self.end_headers()
            return

        if day_str == today_str:
            data = ensure_today_cache_started()  # non-blocking: None while still generating
        else:
            data = load_cache_for_date(day_str)
        # is_generating()도 확인해야 하는 이유: 재생성 중엔 새 캐시가 저장되기 전까지 옛
        # 캐시가 그대로 남아있어서 data가 None이 아님 -> data is None만 보면 재생성 중인데도
        # "생성 중" 표시가 안 뜨고 그냥 옛 페이지가 그대로 보여서 눌렀는지 알 수 없었음.
        generating = day_str == today_str and (data is None or is_generating())

        out = render_html(
            day_str, available_dates(), data, generating=generating, regenerating=generating and data is not None
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        pass  # 조용히
