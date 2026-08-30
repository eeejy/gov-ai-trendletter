# 글꼴

발행물은 두 벌을 나눠 쓴다. `config/settings.yaml` 의 `output.display_font`,
`output.body_font` 로 바꿀 수 있다.

| 쓰임 | 기본값 | 이유 |
| --- | --- | --- |
| `display` — 표지 제목 · 항목 제목 · 배지 · 소제목 | **Paperlogy** | [AX360](https://ax360.kr/national-ax-project/card) 과 같은 인상. 발표용으로 만들어져 큰 글자에서 힘이 있다 |
| `body` — 개조식 본문 · 시사점 · 관련자료 | **Pretendard** | 숫자·괄호·각주가 많은 개조식 문장에서 자간과 줄이 고르다. AX360 도 `body` 기본값은 Pretendard 다 |

Paperlogy 를 본문에까지 쓰면 글자가 굵고 넓어 개조식 본문이 답답해진다.
반대로 표지까지 Pretendard 로 두면 매체 인상이 밋밋해진다. 그래서 나눠 쓴다.

| 파일 | 굵기 | 쓰임 |
| --- | --- | --- |
| `Paperlogy-7Bold.ttf` | 700 | 항목 제목 · 배지 |
| `Paperlogy-9Black.ttf` | 900 | 표지 큰 제목 · 숫자 |
| `Paperlogy-4Regular.ttf` | 400 | body_font 를 paperlogy 로 둘 때 |
| `Paperlogy-8ExtraBold.ttf` | 800 | 예비 |
| `Pretendard-Regular.woff` | 400 | 본문 |
| `Pretendard-Bold.woff` | 700 | 본문 강조 · 시사점 |
| `Pretendard-SemiBold.woff` | 600 | 예비 |

## 라이선스

둘 다 **SIL Open Font License (OFL)** — 글꼴 단독 판매와 라이선스 변경을 뺀
모든 상업적 사용·수정·재배포가 가능하다.

- Paperlogy: https://github.com/fonts-archive/Paperlogy (원문 `PAPERLOGY-LICENSE.md`)
- Pretendard: https://github.com/orioncactus/pretendard

라이선스는 바뀔 수 있으므로 배포처를 주기적으로 확인한다.

## 어떻게 담기는가

`render.fonts_for()` 가 **그 회차 발행물에 실제로 등장하는 글자만** 남겨 woff2 로
자르고 data URI 로 넣는다(`render._subset`).

- 원본은 한 벌에 660KB~1.2MB. 통째로 담으면 회차마다 수 MB 가 붙는다
- 쓰인 글자만 담으면 기본 조합(Paperlogy 2 + Pretendard 2) 기준 **약 200KB**
- 호수·날짜가 바뀌어도 깨지지 않도록 숫자·기호와 상용어는 늘 포함한다(`_ALWAYS`)
- `fonttools` 가 없거나 자르기에 실패하면 조용히 기본 글꼴로 넘어간다
