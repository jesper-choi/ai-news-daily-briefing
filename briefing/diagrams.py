"""D2 어댑터. 요약에 들어있는 d2 코드를 생성 시점에 SVG로 미리 굽는다.

브라우저에서 그리던 mermaid와 달리 여기서 컴파일까지 끝내는 이유:
- 문법이 깨진 다이어그램을 생성 시점에 걸러낼 수 있다. mermaid 때는 브라우저가
  조용히 빼는 수밖에 없어서, 화면을 열어보기 전엔 깨진 줄도 몰랐음.
- 페이지가 CDN에서 라이브러리를 받아오지 않아도 되고, <details>를 펼칠 때
  그리느라 폭이 0이라 찌그러지던 문제도 사라진다.
"""
import os
import re
import shutil
import subprocess

from .config import log
from .d2_preamble import DARK, LIGHT

# launchd로 뜬 프로세스의 PATH는 /usr/bin:/bin:/usr/sbin:/sbin뿐이라 homebrew가 안 보인다.
# which만 믿으면 로그인 후 자동 실행될 때만 다이어그램이 전부 조용히 빠지고, 터미널에서
# 직접 돌리면 멀쩡해서 알아채기 어렵다 -> 흔한 설치 위치를 직접 확인한다.
D2_FALLBACK_PATHS = ("/opt/homebrew/bin/d2", "/usr/local/bin/d2")

# 대소문자를 안 가린다: 모델이 ```D2 로 쓰면 못 잡고 코드가 화면에 그대로 노출됨.
D2_BLOCK = re.compile(r"```d2[ \t]*\n(.*?)```", re.S | re.I)
# 토큰 한도에 걸려 응답이 잘리면 닫는 ```가 없어서 위 정규식이 못 잡는다. 남은 펜스는
# 통째로 지운다 - 어차피 잘린 다이어그램이라 살릴 수도 없고, 노출되면 흉함.
D2_DANGLING = re.compile(r"```d2[ \t]*\n.*\Z", re.S | re.I)
# elk는 프리앰블(d2_preamble)이 지정하지만, 명령줄로도 못박아 둔다.
D2_ARGS = ["--layout=elk", "--omit-version"]
D2_TIMEOUT = 20


def _inline_ready(svg):
    """HTML 문서 안에 그대로 넣을 수 있게 손본다.

    - <?xml ...?> 선언 제거 (문서 중간에 오면 무효)
    - 바깥 <svg>에 viewBox만 있고 width/height가 없어서, 그대로 두면 브라우저가
      컨테이너 폭에 맞춰 늘리거나 줄인다. 넓은 그림이 축소돼 글씨가 안 보이던 게
      mermaid 때 겪은 바로 그 문제 -> 실제 크기를 박아준다.
    """
    svg = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", svg)
    head = re.search(r"<svg[^>]*>", svg)
    if head and "width=" not in head.group(0):
        box = re.search(r'viewBox="[\d.-]+ [\d.-]+ ([\d.]+) ([\d.]+)"', head.group(0))
        if box:
            w, h = box.group(1), box.group(2)
            svg = svg.replace(head.group(0), head.group(0)[:-1] + f' width="{w}" height="{h}">', 1)
    return svg


def d2_bin():
    """d2 실행 파일 경로. PATH에 없으면 흔한 설치 위치까지 본다. 없으면 None."""
    return (shutil.which("d2")
            or next((p for p in D2_FALLBACK_PATHS if os.access(p, os.X_OK)), None))


def _compile(binary, code, preamble):
    """프리앰블 + 코드를 d2에 넣어 SVG 하나를 얻는다. 실패하면 (None, 사유)."""
    try:
        done = subprocess.run(
            [binary, *D2_ARGS, "-", "-"],
            input=preamble + "\n" + code, capture_output=True, text=True, timeout=D2_TIMEOUT,
        )
    except subprocess.SubprocessError as e:
        return None, f"d2 실행 실패: {type(e).__name__}"
    if done.returncode != 0 or "<svg" not in done.stdout:
        return None, done.stderr.strip()[:120]
    svg = _inline_ready(done.stdout)
    if "<script" in svg.lower():
        # d2가 스크립트를 내보낼 일은 없지만, LLM이 쓴 라벨이 그대로 흘러들어오는
        # 경로라 페이지에 raw로 심기 전에 한 번 막아둔다.
        return None, "출력에 script가 있음"
    return svg, ""


def render_d2(code):
    """d2 소스를 (라이트 SVG, 다크 SVG)로. 하나라도 실패하면 None.

    두 번 굽는 이유: 디자인 시스템이 색을 hex로 명시하는데, 그러면 d2의 --dark-theme이
    안 먹는다(테마가 명시 스타일을 못 이김). 페이지 쪽 CSS가 둘 중 하나만 보여준다.
    """
    binary = d2_bin()
    if not binary:
        log("그림", "d2를 찾지 못해 다이어그램을 건너뜁니다 (brew install d2)")
        return None
    light, why = _compile(binary, code, LIGHT)
    if not light:
        log("그림", f"d2 컴파일 실패(이 그림은 뺌): {why}")
        return None
    dark, why = _compile(binary, code, DARK)
    if not dark:
        log("그림", f"다크 렌더 실패(이 그림은 뺌): {why}")
        return None
    return light, dark


# ponytail: SVG마다 서브셋 폰트가 따로 들어가서 그림 하나에 20~30KB. 하루치 40여 개면
# 페이지가 1MB쯤 되는데 localhost라 체감이 없어 그냥 둔다. 무거워지면 폰트를 페이지에
# 한 번만 넣고 SVG에선 빼는 쪽으로.
def bake_diagrams(text):
    """요약 본문의 ```d2 블록을 전부 ```d2svg(완성된 SVG)로 바꿔서 돌려준다.
    컴파일이 안 되는 블록은 통째로 지운다 - 코드가 그대로 노출되는 것보단 낫다."""
    def replace(m):
        code = m.group(1).strip()
        if not code:
            return ""
        pair = render_d2(code)
        if not pair:
            return ""
        light, dark = pair
        # 한 블록 안에 두 장을 넣고 CSS(prefers-color-scheme)가 하나만 보여준다
        return (f'```d2svg\n<span class="d2-light">{light}</span>'
                f'<span class="d2-dark">{dark}</span>\n```')

    return D2_DANGLING.sub("", D2_BLOCK.sub(replace, text)).rstrip()


def strip_diagrams(text):
    """d2 블록을 컴파일하지 않고 그냥 제거 (그림이 들어갈 자리가 아닌 곳용)."""
    return D2_DANGLING.sub("", D2_BLOCK.sub("", text)).strip()
