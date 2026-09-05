# AI Daily Briefing

[GeekNews](https://news.hada.io/)와 [Hacker News](https://news.ycombinator.com/)를 매일 훑어서 AI 관련성 높은 글만 골라, Gemini로 한국어 요약(짧은 요약 + 상세 요약)까지 붙여 `localhost`로 보여주는 개인용 크롤러 겸 서버.

- 각 소스에서 상위 20개 후보 중 AI 관련성 순으로 10개씩 선별 (Gemini 판단)
- 원문 본문을 크롤링해 요약 (실패 시 목록 페이지 요약으로 대체)
- 날짜별로 캐싱 — 한 번 생성된 날짜는 재크롤링 없이 즉시 로드
- 페이지에서 날짜 선택 / 오늘자 다시 생성 가능

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
brew install d2        # 요약 속 다이어그램 렌더링용
cp .env.example .env   # GOOGLE_API_KEY=... 채워넣기
```

`d2`는 요약에 들어가는 다이어그램을 생성 시점에 SVG로 굽는 데 쓴다. 없어도 서버는
정상 동작하고 다이어그램만 빠진다. 색과 레이아웃은 `briefing/d2_preamble.py`가
정하고(Apple HIG 계열 디자인 시스템), 모델은 클래스 이름만 고른다 — 노드마다 색을
지어내면 알록달록한 '생성된 티'가 나기 때문.

`GOOGLE_API_KEY`는 [Google AI Studio](https://aistudio.google.com/apikey)에서 발급받은 Gemini API 키. 키가 없으면 서버는 뜨지만 요약 없이 크롤링 결과만 보여준다.

## 실행

**터미널에서 바로 실행:**

```bash
.venv/bin/python3 server.py
```

브라우저에서 http://localhost:8787 접속. 오늘자 브리핑은 **아침 7시 이후**에 자동으로 만들어진다 (보통 10분 남짓). 그 시각에 맥이 꺼져 있거나 자고 있었으면, 켜진 뒤 10분 안에 시작한다. 더 일찍 보고 싶으면 페이지의 "↻ 다시 생성"을 누르면 시각과 무관하게 바로 만든다.

**macOS 메뉴바 앱으로 실행 (권장):**

```bash
.venv/bin/python3 menubar_app.py
```

서버가 백그라운드로 뜨고 브라우저가 자동으로 열림. 상단 메뉴바 아이콘에서 "브라우저에서 열기" / "서버 끄고 종료" 가능.

## 로그인할 때 자동으로 띄우기 (선택)

`~/Library/LaunchAgents/com.jesper.ai-daily-briefing.plist`에 아래처럼 등록해두면 로그인 시 메뉴바 앱이 자동 실행된다 (경로는 실제 클론 위치에 맞게 수정):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jesper.ai-daily-briefing</string>
  <key>ProgramArguments</key>
  <array>
    <string>/절대/경로/.venv/bin/python3</string>
    <string>/절대/경로/menubar_app.py</string>
  </array>
  <key>WorkingDirectory</key><string>/절대/경로</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/절대/경로/menubar.log</string>
  <key>StandardErrorPath</key><string>/절대/경로/menubar.log</string>
</dict>
</plist>
```

`KeepAlive`는 일부러 넣지 않는다 — 넣으면 메뉴바에서 종료해도 launchd가 바로 되살려서 "종료"가 안 먹힌다.

등록/반영: `launchctl load ~/Library/LaunchAgents/com.jesper.ai-daily-briefing.plist` (수정 후에는 `unload` 후 다시 `load`).

## 사용법

- 상단 드롭다운으로 과거 날짜 선택
- 오늘 날짜에서 "↻ 다시 생성" 버튼으로 오늘자를 재크롤링 (기존 캐시 무시하고 새로 생성)
- 각 글 카드의 "전체 요약 읽기"로 상세 요약 펼쳐보기, "원문 보기"로 원문 이동

## 생성 중 절전

생성은 자정 직후에 자동으로 도는데, 그때 맥이 자면 프로세스가 얼어붙고 진행 중이던
HTTPS 연결이 끊긴다. 요청 타임아웃으로는 못 막는다 — 멈춰 있는 동안엔 타이머도 같이
멈춘다. 그래서 생성 중에는 `caffeinate -i -s`로 절전을 막는다(`-s`는 전원이 연결돼
있을 때만 유효하므로, 자동 생성을 쓸 거면 밤에 전원을 꽂아두는 게 좋다).

자동 생성을 아침 7시로 옮긴 것도 같은 이유다 — 자정에 돌리면 맥이 자는 시간과 정면으로 겹친다.

**뚜껑을 덮으면** 절전 방지로는 막히지 않는다. 그래서 막는 대신 **알아채고 접는다**:
감시 스레드가 30초마다 시계를 보다가 2분 넘는 공백이 생기면(=그동안 프로세스가 얼어
있었다는 뜻) 생성을 중단한다. 캐시를 저장하지 않으므로 다음 확인(최대 30분)이나 다음
접속 때 처음부터 다시 만든다.

절반만 된 브리핑을 남기느니 깨어 있을 때 12분 만에 온전히 만드는 편이 낫다 — 예전엔
잠들었다 깨기를 반복하며 11시간 동안 끊긴 연결로 실패만 쌓은 날이 있었다.

```
[생성] 20분 공백 - 맥이 잔 것으로 보고 생성을 접습니다
[생성] 중단: 맥이 자는 동안 연결이 끊겼습니다 (34분 진행) -> 다음에 처음부터 다시
```

## 데이터

캐시는 `cache/YYYY-MM-DD.json`에 저장된다 (`.gitignore`에 포함, 커밋되지 않음).

## 구조

계층별로 나뉘어 있고, 위가 아래를 알고 아래는 위를 모른다.

| | 역할 |
|---|---|
| `server.py` | 조립 — 소켓 바인드, 백그라운드 스레드 기동 |
| `briefing/web.py` | 표현 — 페이지 HTML, HTTP 핸들러 |
| `briefing/service.py` | 응용 — 브리핑 생성 흐름, "생성 중" 상태, 절전 방지 |
| `briefing/summarize.py` | 도메인 — 무엇을 고르고 어떻게 요약할지의 규칙 |
| `briefing/repository.py` | 저장소 — 날짜별 캐시 파일 |
| `briefing/sources.py` | 어댑터 — 크롤링 (GeekNews / HN / 뉴스레터 / 기사 본문) |
| `briefing/llm.py` | 어댑터 — Gemini 호출, 모델 티어 로테이션, 쿼터 처리 |
| `briefing/diagrams.py` | 어댑터 — d2 코드를 SVG로 컴파일 |
| `briefing/d2_preamble.py` | 다이어그램 디자인 시스템 (색·레이아웃) |
| `briefing/config.py` | 공유 — 설정과 로그 |

사이트가 개편되거나 모델이 바뀌면 어댑터만, 화면이 바뀌면 `web.py`만 고치면 된다.

자체 점검: `.venv/bin/python3 test_server.py` (네트워크·API 키 없이 돈다)

## 로그 보기

`menubar.log`에 태그별로 한 줄씩 쌓인다.

```bash
grep '\[gemini\]' menubar.log          # LLM 호출: 모델 / 소요시간 / 성공여부 / 오늘 누적
grep '\[생성\]'   menubar.log          # 생성 시작·완료 (소요시간, 항목 수, 요약 실패 수)
grep '\[출처\]'   menubar.log          # 크롤링 결과와 실패
grep '\[요약\]'   menubar.log          # 개별 요약 실패
grep '\[그림\]'   menubar.log          # d2 컴파일 실패

# 오늘 모델별로 몇 번 성공했나 (RPD 소진 확인)
grep '\[gemini\]' menubar.log | grep ' ok ' | awk '{print $4}' | sort | uniq -c
```

429는 `quotaId`가 같이 찍혀서 `...PerDay...`(하루치 소진, 자정까지 못 씀)와
`...PerMinute...`(잠깐 몰린 것, 곧 풀림)를 구분할 수 있다.
