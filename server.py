#!/usr/bin/env python3
"""GeekNews(news.hada.io) + Hacker News + AI Engineering 뉴스레터를 매일 훑어서 AI
관련성 높은 글만 골라 한국어로 요약해 localhost로 보여주는 서버.

여기는 조립만 한다. 실제 로직은 briefing/ 아래 계층별로 나뉘어 있음 (briefing/__init__.py 참고).

사용법:
    .env에 GOOGLE_API_KEY=... 설정
    .venv/bin/python3 server.py
    -> http://localhost:8787
"""
import threading
from http.server import ThreadingHTTPServer

from briefing.config import API_KEY, PORT, log
from briefing.service import _daily_autogen_loop, ensure_today_cache_started
from briefing.web import Handler

__all__ = ["PORT", "start_server", "main"]


def start_server():
    """소켓을 바인드하고(포트 충돌이면 여기서 바로 OSError) 백그라운드 준비 작업을 시작한
    뒤, 아직 serve_forever는 부르지 않은 서버를 반환한다. 바인드와 blocking accept 루프를
    분리해둬야 호출부(메뉴바 앱 등)가 '포트 이미 사용 중' 같은 실패를 그 자리에서 바로
    알아채고 사용자에게 보여줄 수 있다 - 안 그러면 백그라운드 스레드 안에서 조용히 죽어서
    앱은 멀쩡해 보이는데 서버만 안 뜨는 상태가 됨."""
    if not API_KEY:
        log("경고", "GOOGLE_API_KEY가 설정되지 않았습니다. .env에 GOOGLE_API_KEY=... 를 넣어주세요.")
    httpd = ThreadingHTTPServer(("localhost", PORT), Handler)
    log("서버", f"http://localhost:{PORT} 서비스 시작 (오늘자 캐시가 없으면 즉시 자동 생성, "
                f"날짜가 바뀌어도 서버가 떠 있으면 다음날 것도 자동 생성)")
    ensure_today_cache_started()  # 접속 안 해도 서버 켜지자마자 바로 생성 시작
    threading.Thread(target=_daily_autogen_loop, daemon=True).start()
    return httpd


def main():
    """CLI에서 직접 실행할 때: 바인드하고 이 스레드에서 블로킹으로 서비스."""
    start_server().serve_forever()


if __name__ == "__main__":
    main()
