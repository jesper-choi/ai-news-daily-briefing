"""AI Daily Briefing.

계층 구조 (위가 아래를 알고, 아래는 위를 모른다):

    server.py    조립 - 소켓 바인드, 백그라운드 스레드 기동
    web          표현 - 페이지 HTML, HTTP 핸들러
    service      응용 - '오늘자 브리핑 만들기' 흐름과 생성 중 상태
    summarize    도메인 - 무엇을 고르고 어떻게 요약할지의 규칙
    repository   저장소 - 날짜별 캐시 파일
    sources/llm  어댑터 - 바깥 세상(크롤링, Gemini)
    config       공유 - 설정과 로그

바깥이 바뀌면(사이트 개편, 모델 교체) 어댑터만 고치면 되고, 화면이 바뀌면 web만
고치면 된다. 도메인 규칙은 requests도 genai도 모른다.
"""
