#!/usr/bin/env python3
"""GeekNews(news.hada.io) + Hacker News를 매일 각각 20개씩 훑어서 AI 관련성 높은 10개씩만
골라 한국어로 요약해 localhost로 보여주는 서버. (URL 겹치면 GeekNews 우선, HN 쪽에서 제외)

사용법:
    .env에 GOOGLE_API_KEY=... 설정
    .venv/bin/python3 server.py
    -> http://localhost:8787
"""
import glob
import html
import json
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai.errors import APIError


def load_dotenv(path=os.path.join(os.path.dirname(__file__), ".env")):
    """.env의 KEY=VALUE를 환경변수로 로드 (이미 설정된 값은 덮어쓰지 않음)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv()

BASE_URL = "https://news.hada.io/"
HN_URL = "https://news.ycombinator.com/"
CANDIDATE_N = 20  # 각 소스에서 우선 훑어볼 후보 개수
PICK_N = 10  # 그중 AI 관련성 순으로 골라낼 개수
PORT = 8787
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
API_KEY = os.environ.get("GOOGLE_API_KEY")
MODEL = "gemini-3.5-flash"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-clrawler/1.0)"}
gemini = genai.Client(api_key=API_KEY) if API_KEY else None
# ponytail: free-tier gemini-3.5-flash allows ~5 requests/min, so we pace calls with
# a global last-call timestamp. Single-process only; a real multi-worker deployment
# needs a shared rate limiter (e.g. redis) instead.
_MIN_INTERVAL = 13  # seconds between Gemini calls
_last_call = 0.0


def fetch_top20(n=CANDIDATE_N):
    resp = requests.get(BASE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for row in soup.select("div.topic_row")[:n]:
        title_a = row.select_one(".topictitle a[href]")
        if not title_a:
            continue
        desc_a = row.select_one(".topicdesc a")
        points_span = row.select_one(".topicinfo span[id^=tp]")
        domain = row.select_one(".topicurl")
        topic_id = row.get("data-topic-state-id", "")
        items.append({
            "rank": len(items) + 1,
            "title": title_a.get_text(strip=True),
            "link": title_a["href"],
            "domain": domain.get_text(strip=True).strip("()") if domain else "",
            "excerpt": desc_a.get_text(strip=True) if desc_a else "",
            "points": points_span.get_text(strip=True) if points_span else "0",
            "discuss_url": f"https://news.hada.io/topic?id={topic_id}",
        })
    return items


def fetch_hn_top(n=CANDIDATE_N):
    resp = requests.get(HN_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for row in soup.select("tr.athing")[:n]:
        title_a = row.select_one(".titleline a")
        if not title_a:
            continue
        item_id = row.get("id", "")
        site = row.select_one(".sitestr")
        subtext = row.find_next_sibling("tr")
        score_el = subtext.select_one(".score") if subtext else None
        items.append({
            "rank": len(items) + 1,
            "title": title_a.get_text(strip=True),
            "link": title_a["href"],
            "domain": site.get_text(strip=True) if site else "news.ycombinator.com",
            "excerpt": "",
            "points": score_el.get_text(strip=True).split()[0] if score_el else "0",
            "discuss_url": f"https://news.ycombinator.com/item?id={item_id}",
        })
    return items


def fetch_article_text(url, max_chars=12000):
    """원문 기사 본문을 최선을 다해 추출. 실패하면 None (호출부에서 excerpt로 대체)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        container = soup.find("article") or soup.body
        if not container:
            return None
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = " ".join(p for p in paragraphs if len(p) > 30)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars] if text else None
    except requests.RequestException:
        return None


def _split_summary(text):
    """[ABSTRACT]/[DETAIL] 마커로 응답을 분리. 마커가 없으면 앞부분을 abstract로 대체."""
    m = re.search(r"\[ABSTRACT\]\s*(.*?)\s*\[DETAIL\]\s*(.*)", text, re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    abstract = text[:180].rsplit(" ", 1)[0] + "…" if len(text) > 180 else text
    return abstract, text


def _gemini_call(prompt, max_output_tokens=16000):
    """페이싱 + 429/503 재시도가 포함된 Gemini 호출. 텍스트를 반환하거나 APIError를 던짐."""
    global _last_call
    for attempt in range(3):
        wait = _MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            resp = gemini.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=max_output_tokens,
                    # 요약 작업엔 내부 reasoning이 불필요 + 토큰 예산을 갉아먹어 본문이 잘리는
                    # 원인이었음 -> 끄고 그만큼을 전부 보이는 텍스트에 씀
                    thinking_config=genai.types.ThinkingConfig(thinking_budget=0),
                ),
            )
            return resp.text.strip()
        except APIError as e:
            if e.code in (429, 503) and attempt < 2:
                m = re.search(r"retry in ([\d.]+)s", str(e))
                time.sleep(float(m.group(1)) + 1 if m else 15)
                continue
            raise
        except Exception:
            # httpx/network-level hiccups (dropped connection, DNS blip, etc.) aren't APIError
            # but are just as transient -> retry the same way instead of killing the request.
            if attempt < 2:
                time.sleep(5)
                continue
            raise


def select_ai_related(items, n=PICK_N):
    """items를 AI 관련성 순으로 정렬시켜 상위 n개만 골라 반환 (Gemini 판단).
    명확히 AI 관련인 게 n개보다 적어도, 상대적으로 관련성 높은 순으로 n개를 채운다."""
    if not gemini or not items:
        return items[:n]

    listing = "\n".join(f"{i + 1}. {it['title']} - {it['excerpt']}" for i, it in enumerate(items))
    prompt = (
        f"다음은 오늘의 뉴스/토픽 {len(items)}개 목록입니다.\n\n{listing}\n\n"
        "이 목록을 'AI 관련성'이 높은 순서로 전부 정렬해줘. AI 관련성은 넓게 판단해줘 - LLM/생성형 AI/"
        "멀티모달 같은 핵심 모델·기술뿐 아니라, AI 에이전트·코딩 에이전트(예: 병렬로 코딩하는 에이전트 "
        "오픈소스 툴)·에이전틱 워크플로우·AI 개발도구·AI 인프라(파인튜닝, 추론 최적화, RAG, 벡터DB 등)·"
        "AI 정책/산업/비즈니스 동향까지 전부 AI 관련으로 포함해줘. 제목에 'AI'라는 단어가 없어도 "
        "실질적으로 AI/에이전트 기술을 다루면 관련 있는 것으로 쳐줘.\n\n"
        f"명확하게 AI 관련인 게 {n}개보다 적어도 상관없이, 상대적으로 관련성이 높아 보이는 순서로 "
        f"순위를 매겨서 상위 {n}개의 번호만 답해줘. 설명 없이 번호만 쉼표로 구분해서. 예: 3, 1, 7, 12, 5"
    )
    try:
        text = _gemini_call(prompt, max_output_tokens=200)
    except Exception:
        return items[:n]

    picked, seen = [], set()
    for tok in re.findall(r"\d+", text):
        idx = int(tok) - 1
        if 0 <= idx < len(items) and idx not in seen:
            seen.add(idx)
            picked.append(items[idx])
        if len(picked) >= n:
            break
    return picked or items[:n]


def summarize_ko(item, article_text):
    if not gemini:
        msg = "GOOGLE_API_KEY가 설정되지 않아 요약을 생성할 수 없습니다."
        return {"abstract": msg, "detail": msg}

    source = article_text or item["excerpt"] or item["title"]
    prompt = (
        f"다음은 '{item['title']}' 기사의 원문 발췌(또는 목록 페이지 요약)입니다. "
        "원문이 외국어면 자연스러운 한국어로 풀어써줘.\n\n"
        f"---\n{source}\n---\n\n"
        "아래 형식을 정확히 지켜서 응답해줘. 마크다운 기호나 다른 설명은 넣지 마.\n\n"
        "[ABSTRACT]\n"
        "2~3문장으로 핵심만 압축한 요약\n\n"
        "[DETAIL]\n"
        "A4 용지 4~6장 분량(한국어 기준 약 4500~9000자)의 아주 상세한 요약. 배경과 맥락, "
        "핵심 내용을 항목별로 풍부하게, 구체적인 근거·수치·인용·사례, 관련 배경지식, "
        "다양한 시각(찬반/한계점 등), 의의와 시사점까지 깊이 있게 다루는 여러 문단의 글. "
        "짧게 요약하지 말고 충분히 길고 읽을거리가 되도록 풀어써줘. 문단 사이는 빈 줄로 구분해줘. "
        "본문만 작성하고, '더 필요하신가요?' 같은 되묻는 말이나 인사말 등 대화체 멘트는 절대 넣지 마."
    )
    try:
        abstract, detail = _split_summary(_gemini_call(prompt, max_output_tokens=16000))
        return {"abstract": abstract, "detail": detail}
    except Exception as e:
        msg = f"요약 생성 실패: {e}"
        return {"abstract": msg, "detail": msg}


def cache_path(day_str):
    return os.path.join(CACHE_DIR, f"{day_str}.json")


def available_dates():
    """캐시가 존재하는 날짜(YYYY-MM-DD) 목록, 최신순."""
    files = glob.glob(cache_path("*"))
    return sorted((os.path.splitext(os.path.basename(f))[0] for f in files), reverse=True)


def load_cache_for_date(day_str):
    """해당 날짜 캐시가 파일로 존재하면 로드, 없으면 None (오늘 캐시 생성은 다루지 않음 -
    그건 ensure_today_cache() / ensure_today_cache_started() 쪽 책임)."""
    path = cache_path(day_str)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def build_section(items):
    """항목 리스트에 원문 크롤링 + Gemini 요약을 채워넣는다 (in place, 리스트도 반환)."""
    if not items:
        return items
    # Article bodies are plain HTTP fetches (no rate limit) -> safe to parallelize.
    with ThreadPoolExecutor(max_workers=6) as pool:
        article_texts = list(pool.map(lambda it: fetch_article_text(it["link"]), items))

    # Gemini free-tier caps requests/min -> summarize sequentially (summarize_ko paces itself).
    for item, article_text in zip(items, article_texts):
        result = summarize_ko(item, article_text)
        item["abstract"] = result["abstract"]
        item["detail"] = result["detail"]
        item["summary_source"] = "article" if article_text else "listing"
    return items


def _build_today_data():
    """오늘자 데이터를 실제로 크롤링+요약해서 만든다 (몇 분 걸림). 디스크에 쓰지 않고 반환만."""
    today = date.today().isoformat()
    geeknews = select_ai_related(fetch_top20())
    seen_links = {it["link"] for it in geeknews}
    # GeekNews often re-curates the same story from HN -> drop exact URL dupes, HN loses the tie
    hn_candidates = [it for it in fetch_hn_top() if it["link"] not in seen_links]
    hn = select_ai_related(hn_candidates)
    for i, it in enumerate(geeknews, 1):
        it["rank"] = i
    for i, it in enumerate(hn, 1):
        it["rank"] = i

    build_section(geeknews)
    build_section(hn)

    return {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "sections": [
            {"key": "geeknews", "label": "GeekNews", "items": geeknews},
            {"key": "hn", "label": "Hacker News", "items": hn},
        ],
    }


def _save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = cache_path(data["date"])
    tmp = path + ".tmp"
    # write to a temp file + atomic rename so concurrent readers (the polling loading page)
    # never see a half-written JSON file.
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def ensure_today_cache():
    """블로킹: 오늘 캐시가 있으면 로드, 없으면 지금 당장 만들어서 반환 (CLI/스크립트용)."""
    today = date.today().isoformat()
    cached = load_cache_for_date(today)
    if cached is not None:
        return cached
    data = _build_today_data()
    _save_cache(data)
    return data


_generation_lock = threading.Lock()
_generating = False


def ensure_today_cache_started(force=False):
    """논블로킹(HTTP 핸들러용): 오늘 캐시가 있으면 반환. 없으면(또는 force=True로 재생성
    요청이면) 백그라운드 스레드로 생성을 시작해두고 None을 반환 -> 호출부는 '생성 중'
    페이지를 즉시 보여줄 수 있다."""
    today = date.today().isoformat()
    if not force:
        cached = load_cache_for_date(today)
        if cached is not None:
            return cached

    global _generating
    with _generation_lock:
        already_running = _generating
        _generating = True

    if not already_running:
        def _run():
            global _generating
            try:
                _save_cache(_build_today_data())
            except Exception as e:
                # 실패해도 서버는 안 죽음 - _generating만 풀어주면 다음 새로고침 때 재시도됨
                print(f"오늘자 캐시 생성 실패, 다음 요청에서 재시도: {e}")
            finally:
                with _generation_lock:
                    _generating = False

        threading.Thread(target=_run, daemon=True).start()
    return None


def paragraphs_html(text):
    """빈 줄로 구분된 텍스트를 <p> 태그들로 변환 (내용은 escape)."""
    parts = [html.escape(p.strip()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{p}</p>" for p in parts) or f"<p>{html.escape(text)}</p>"


def date_picker_html(selected, available):
    # 오늘자가 아직 생성 중이면 available(=캐시 파일이 실제 존재하는 날짜)엔 없음 ->
    # 그래도 드롭다운에 "선택된 상태"로는 보이게 합성해서 넣어줌. 이전 날짜들은 그대로 선택 가능.
    dates = available if selected in available else [selected, *available]
    options = "".join(
        f'<option value="{d}"{" selected" if d == selected else ""}>'
        f'{d}{" (오늘)" if d == date.today().isoformat() else ""}'
        f'{" · 생성 중" if d == selected and d not in available else ""}</option>'
        for d in dates
    )
    return f"""<select class="date-picker" onchange="location.href='/?date='+this.value">{options}</select>"""


def render_html(day_str, available, data, generating=False):
    refresh_tag = '<meta http-equiv="refresh" content="6">' if generating else ""
    if generating:
        body = """
        <div class="generating">
          <div class="pulse-dots"><span></span><span></span><span></span></div>
          <p>오늘의 브리핑을 만들고 있어요…</p>
          <p class="hint">GeekNews · Hacker News를 훑어서 AI 관련 기사를 고르고 있어요.<br>보통 3~6분 걸려요. 이 페이지는 6초마다 자동으로 새로고침돼요.</p>
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
                cards.append(f"""
        <article class="entry">
          <p class="index">{it['rank']:02d}</p>
          <h3>{title}</h3>
          <div class="meta">
            <span>{domain}</span>
            <span class="dot">·</span>
            <span>▲ {it['points']}</span>
            <span class="dot">·</span>
            <a class="meta-link" href="{discuss_url}" target="_blank" rel="noopener">토론 보기</a>
          </div>
          <p class="abstract">{abstract}</p>
          <details class="detail-toggle">
            <summary>전체 요약 읽기</summary>
            <div class="detail-text">{detail_html}</div>
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
<title>AI 데일리 브리핑 · {day_str}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI2NCIgaGVpZ2h0PSI2NCIgdmlld0JveD0iMCAwIDY0IDY0Ij4KPHJlY3QgeD0iMyIgeT0iMyIgd2lkdGg9IjU4IiBoZWlnaHQ9IjU4IiByeD0iMTQiIGZpbGw9IiNmZmZkZjgiIHN0cm9rZT0iI2U2ZTBkMiIgc3Ryb2tlLXdpZHRoPSIyIi8+Cjx0ZXh0IHg9IjI3IiB5PSI0NiIgZm9udC1mYW1pbHk9Ikdlb3JnaWEsICdJb3dhbiBPbGQgU3R5bGUnLCAnUGFsYXRpbm8gTGlub3R5cGUnLCBzZXJpZiIgZm9udC1zdHlsZT0iaXRhbGljIiBmb250LXdlaWdodD0iNzAwIiBmb250LXNpemU9IjQwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSIjMmY1ZDhhIj5BPC90ZXh0Pgo8Y2lyY2xlIGN4PSI0NSIgY3k9IjE2IiByPSI1IiBmaWxsPSIjZTA3OTNhIi8+Cjwvc3ZnPgo=">
{refresh_tag}
<style>
  :root {{
    --bg: #f7f4ed; --card: #fffdf8; --text: #2b2820; --muted: #837a68;
    --border: #e6e0d2; --accent: #2f5d8a; --accent-soft: #dde7f0;
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
    max-width: 700px; margin: 0 auto; padding: 4.5rem 1.5rem 2.5rem;
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
  main {{ max-width: 700px; margin: 0 auto; padding: .5rem 1.5rem 6rem; }}
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
  .actions {{ display: flex; }}
  .btn-primary {{
    display: inline-flex; align-items: center; gap: .4rem; padding: .6rem 1.3rem;
    border-radius: 8px; background: var(--accent); color: #fff9f2; text-decoration: none;
    font-size: .85rem; font-weight: 700; transition: opacity .15s ease;
  }}
  .btn-primary:hover {{ opacity: .85; }}
  footer {{
    max-width: 700px; margin: 0 auto; padding: 0 1.5rem 4rem; text-align: center;
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
    <h1>AI 데일리 브리핑</h1>
    <p>{day_str}{f' · 총 {item_count}개 · {generated_at} 생성' if data else ''}</p>
  </header>
  <main>{body}</main>
  <footer>GeekNews · Hacker News 기반 · Gemini로 생성한 원문 요약</footer>
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
        generating = data is None and day_str == today_str

        out = render_html(day_str, available_dates(), data, generating=generating).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        pass  # 조용히


def _daily_autogen_loop():
    """서버가 계속 떠 있으면, 아무도 접속 안 해도 날짜가 바뀌는 순간(자정 이후) 알아서
    그날 캐시 생성을 시작해준다. 이미 있거나 생성 중이면 그냥 아무것도 안 하는 가벼운 체크."""
    while True:
        time.sleep(1800)  # 30분마다 체크
        try:
            ensure_today_cache_started()
        except Exception as e:
            print(f"자동 생성 체크 중 오류(다음 주기에 재시도): {e}")


def main():
    """서버를 이 프로세스/스레드에서 블로킹으로 띄운다 (CLI 직접 실행 및 메뉴바 앱에서 공용)."""
    if not API_KEY:
        print("경고: GOOGLE_API_KEY가 설정되지 않았습니다. .env에 GOOGLE_API_KEY=... 를 넣어주세요.")
    print(f"http://localhost:{PORT} 에서 서비스 중 (오늘자 캐시가 없으면 서버 켜지는 즉시 자동 생성 시작, "
          f"날짜 바뀌어도 서버가 떠있으면 알아서 다음날 것도 자동 생성됨)")
    ensure_today_cache_started()  # 접속 안 해도 서버 켜지자마자 바로 생성 시작
    threading.Thread(target=_daily_autogen_loop, daemon=True).start()
    ThreadingHTTPServer(("localhost", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
