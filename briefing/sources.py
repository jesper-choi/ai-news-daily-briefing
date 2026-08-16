"""크롤링 어댑터. GeekNews / Hacker News / 뉴스레터 목록과 기사 본문을 가져온다.
바깥 사이트가 죽거나 형식이 바뀌는 건 여기서 흡수하고, 위 계층엔 dict 리스트만 넘긴다."""
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

from .config import (BASE_URL, CANDIDATE_N, HEADERS, HN_URL, NEWSLETTER_DAYS,
                     NEWSLETTER_URL, log)


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

def _beehiiv_post_meta(url):
    """포스트 페이지의 JSON-LD에서 (발행일, 제목, 설명)을 뽑는다. 실패하면 None.
    beehiiv는 RSS를 안 주고 목록 페이지에도 날짜가 없어서 글마다 한 번씩 열어봐야 함."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            data = json.loads(script.get_text())
            if data.get("@type") == "Article" and data.get("datePublished"):
                return data["datePublished"][:10], data.get("headline", ""), data.get("description", "")
    except Exception:  # 글 하나 못 읽었다고 뉴스레터 섹션 전체를 날리지 않는다
        return None
    return None

def fetch_newsletter_recent(days=NEWSLETTER_DAYS, scan=8):
    """AI Engineering 뉴스레터에서 최근 days일 안에 나온 글만 가져온다.
    주 2회꼴이라 보통 1~3개이고, 그 주에 글이 없으면 빈 리스트(=섹션이 통째로 숨겨짐)."""
    try:
        resp = requests.get(NEWSLETTER_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log("출처", f"뉴스레터 목록을 가져오지 못함(이 섹션은 건너뜀): {type(e).__name__}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    hrefs = list(dict.fromkeys(
        a["href"] for a in soup.select("a[href]") if "/p/" in a["href"]
    ))
    # 목록이 최신순이라 앞쪽 몇 개만 봐도 충분함 (전부 열면 매번 12번씩 요청하게 됨)
    urls = [urllib.parse.urljoin(NEWSLETTER_URL, h) for h in hrefs[:scan]]

    with ThreadPoolExecutor(max_workers=6) as pool:
        metas = list(pool.map(_beehiiv_post_meta, urls))

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    items = []
    for url, meta in zip(urls, metas):
        if not meta:
            continue
        published, headline, description = meta
        if published < cutoff:
            continue
        items.append({
            "rank": len(items) + 1,
            "title": headline or url.rsplit("/", 1)[-1].replace("-", " "),
            "link": url,
            "domain": "aiengineering.beehiiv.com",
            "excerpt": description,
            "points": "",  # 뉴스레터엔 추천수/토론 스레드가 없음 -> 카드에서 발행일로 대체
            "discuss_url": "",
            "published": published,
        })
    return items

# 봇 차단/로그인/JS 전용 페이지는 200 OK로 "인증하세요" 같은 안내문만 돌려줌. 그게
# 본문으로 잡히면 기사 내용인 줄 알고 요약해버려서(openreview가 딱 이 케이스였음)
# 짧은 추출물에 한해 이런 문구가 보이면 추출 실패로 친다. 긴 본문에서는 검사하지
# 않으므로 캡차를 '다루는' 진짜 기사는 걸러지지 않음.
_BOT_WALL_MARKERS = (
    "verification", "captcha", "are you a robot", "enable javascript",
    "javascript is required", "javascript to continue", "access denied",
    "sign in to", "log in to", "subscribe to continue", "cookies to continue",
)

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
        if not text:
            return None
        if len(text) < 600 and any(m in text.lower() for m in _BOT_WALL_MARKERS):
            return None
        return text[:max_chars]
    except Exception as e:
        # RequestException만 잡으면 부족하다. 파싱/디코딩에서 나는 다른 예외는 그대로
        # 올라가 pool.map -> build_section -> 생성 전체를 죽인다. 기사 하나가 이상하다고
        # 그날 브리핑을 통째로 날릴 이유는 없음 -> 그 기사만 본문 없이 간다.
        log("출처", f"본문 추출 실패({url[:60]}): {type(e).__name__}: {str(e)[:60]}")
        return None

def fetch_geeknews_topic(discuss_url, max_chars=12000):
    """GeekNews 토픽 페이지의 자체 한국어 요약을 가져온다. 원문이 봇 차단/유튜브 등으로
    막혔을 때의 대체 소스 - 목록 excerpt는 이 요약의 첫 줄만 잘라온 거라 90자뿐이지만,
    토픽 페이지엔 5천~1만자짜리 정리가 통째로 있다."""
    try:
        resp = requests.get(discuss_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        container = soup.select_one("div.topic_contents")
        if not container:
            return None
        text = re.sub(r"\s+", " ", container.get_text(" ", strip=True)).strip()
        return text[:max_chars] if text else None
    except Exception as e:  # 위 fetch_article_text와 같은 이유로 넓게 잡는다
        log("출처", f"토픽 페이지 실패({discuss_url[:60]}): {type(e).__name__}: {str(e)[:60]}")
        return None

def fetch_source_text(item):
    """요약에 쓸 본문을 구한다. 원문 -> (GeekNews면) 토픽 페이지 요약 순으로 시도.
    (본문, 출처종류)를 반환하고, 둘 다 실패하면 (None, 'listing')."""
    text = fetch_article_text(item["link"])
    if text:
        return text, "article"
    if "news.hada.io/topic" in item.get("discuss_url", ""):
        topic = fetch_geeknews_topic(item["discuss_url"])
        if topic:
            return topic, "topic"
    return None, "listing"
