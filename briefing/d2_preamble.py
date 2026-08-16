"""d2 다이어그램의 디자인 시스템 프리앰블 (Apple HIG 계열).

`~/.claude/skills/d2-diagram/SKILL.md`의 프리앰블을 그대로 가져온 것. 색은 여기서만
정하고 요약 프롬프트는 클래스 이름만 고르게 한다 - 모델이 노드마다 색을 지어내면
'생성된 티'가 나는 알록달록한 그림이 되기 때문.

라이트/다크를 파일 두 개로 나눠 굽는 이유: 명시적인 hex를 쓰면 d2의 `--dark-theme`이
안 먹는다(테마가 명시 스타일을 못 이김). 그래서 각각 굽고 CSS로 골라 보여준다.
"""

_CONNECTORS = """
(*** -> ***)[*].style.stroke: "{stroke}"
(*** -> ***)[*].style.stroke-width: 1
(*** -> ***)[*].style.font-size: 13
(*** -> ***)[*].style.font-color: "#8E8E93"
(*** -> ***)[*].target-arrowhead.shape: arrow
"""

# 셋뿐인 표면 단계(캔버스 → 트레이 → 카드)와 한 가지 강조색이 이 스타일의 전부다.
LIGHT = """vars: {
  d2-config: {
    layout-engine: elk
    theme-id: 0
    pad: 40
    center: true
    sketch: false
  }
}
style.fill: "#F2F2F7"
""" + _CONNECTORS.format(stroke="#C7C7CC") + """
classes: {
  card:    { style: { fill: "#FFFFFF"; stroke: "#FFFFFF"; font-color: "#1D1D1F"; border-radius: 12; font-size: 15 } }
  tinted:  { style: { fill: "#007AFF"; stroke: "#007AFF"; font-color: "#FFFFFF"; border-radius: 12; font-size: 15; bold: true } }
  fill2:   { style: { fill: "#E9F0FF"; stroke: "#E9F0FF"; font-color: "#0056B3"; border-radius: 12; font-size: 15 } }
  quiet:   { style: { fill: "#EBEBF0"; stroke: "#EBEBF0"; font-color: "#8E8E93"; border-radius: 12; font-size: 15 } }
  outside: { style: { fill: "#F2F2F7"; stroke: "#C7C7CC"; stroke-dash: 3; font-color: "#8E8E93"; border-radius: 12; font-size: 15 } }
  tray:    { style: { fill: "#E5E5EA"; stroke: "#E5E5EA"; font-color: "#8E8E93"; border-radius: 20; font-size: 13 } }
  store:   { shape: cylinder; style: { fill: "#FFFFFF"; stroke: "#FFFFFF"; font-color: "#1D1D1F"; font-size: 15 } }
  queue:   { shape: queue;    style: { fill: "#FFFFFF"; stroke: "#FFFFFF"; font-color: "#1D1D1F"; font-size: 15 } }
  branch:  { shape: diamond;  style: { fill: "#FFFFFF"; stroke: "#FFFFFF"; font-color: "#1D1D1F"; font-size: 15 } }
  pill:    { shape: oval;     style: { fill: "#FFFFFF"; stroke: "#FFFFFF"; font-color: "#1D1D1F"; font-size: 15 } }
  ok:      { style: { fill: "#E6F8EC"; stroke: "#E6F8EC"; font-color: "#1B7D36"; border-radius: 12; font-size: 15 } }
  warn:    { style: { fill: "#FFF4E5"; stroke: "#FFF4E5"; font-color: "#9A5B00"; border-radius: 12; font-size: 15 } }
  danger:  { style: { fill: "#FFECEB"; stroke: "#FFECEB"; font-color: "#C41E14"; border-radius: 12; font-size: 15 } }
}
"""

# 다크는 원본 스킬의 순검정(OLED용) 대신 페이지의 따뜻한 갈색 계열에서 뽑았다. 검정
# 판이 뜬금없이 떠 보이기도 하고, 무엇보다 캔버스가 페이지 배경(#191611)과 너무
# 비슷하면 판 자체가 안 보인다 -> 캔버스 > 트레이 > 카드 순으로 확실히 밝아지게 잡음.
DARK = """vars: {
  d2-config: {
    layout-engine: elk
    theme-id: 200
    pad: 40
    center: true
    sketch: false
  }
}
style.fill: "#241f18"
""" + _CONNECTORS.format(stroke="#6b6152") + """
classes: {
  card:    { style: { fill: "#3b342a"; stroke: "#3b342a"; font-color: "#FFFFFF"; border-radius: 12; font-size: 15 } }
  tinted:  { style: { fill: "#0A84FF"; stroke: "#0A84FF"; font-color: "#FFFFFF"; border-radius: 12; font-size: 15; bold: true } }
  fill2:   { style: { fill: "#0A2647"; stroke: "#0A2647"; font-color: "#64D2FF"; border-radius: 12; font-size: 15 } }
  quiet:   { style: { fill: "#2c2721"; stroke: "#2c2721"; font-color: "#8E8E93"; border-radius: 12; font-size: 15 } }
  outside: { style: { fill: "#241f18"; stroke: "#5b5245"; stroke-dash: 3; font-color: "#8E8E93"; border-radius: 12; font-size: 15 } }
  tray:    { style: { fill: "#2c2721"; stroke: "#2c2721"; font-color: "#8E8E93"; border-radius: 20; font-size: 13 } }
  store:   { shape: cylinder; style: { fill: "#3b342a"; stroke: "#3b342a"; font-color: "#FFFFFF"; font-size: 15 } }
  queue:   { shape: queue;    style: { fill: "#3b342a"; stroke: "#3b342a"; font-color: "#FFFFFF"; font-size: 15 } }
  branch:  { shape: diamond;  style: { fill: "#3b342a"; stroke: "#3b342a"; font-color: "#FFFFFF"; font-size: 15 } }
  pill:    { shape: oval;     style: { fill: "#3b342a"; stroke: "#3b342a"; font-color: "#FFFFFF"; font-size: 15 } }
  ok:      { style: { fill: "#0B2E1A"; stroke: "#0B2E1A"; font-color: "#30D158"; border-radius: 12; font-size: 15 } }
  warn:    { style: { fill: "#332100"; stroke: "#332100"; font-color: "#FF9F0A"; border-radius: 12; font-size: 15 } }
  danger:  { style: { fill: "#3A0F0D"; stroke: "#3A0F0D"; font-color: "#FF453A"; border-radius: 12; font-size: 15 } }
}
"""
