# DART 재무제표 엑셀 변환 유틸리티

이 저장소에는 금융감독원 전자공시시스템(DART) API를 이용해 특정 기업의 모든 재무제표 계정을
5개년 분기별로 내려받아 엑셀로 변환하는 파이썬 스크립트가 포함되어 있습니다.

## 요구 사항

- Python 3.9 이상
- `pip install -r requirements.txt`
- DART API 인증키 (`crtfc_key`)

## 사용법

```bash
python scripts/dart_financials_to_excel.py <API_KEY> "<회사명>" \
  --latest 5 \
  --fs-div CFS \
  --output samsung_financials.xlsx
```

- `<API_KEY>`: DART에서 발급받은 인증키
- `<회사명>`: DART에 등록된 정확한 회사명(예: `삼성전자`)
- `--latest`: 최근 몇 개년을 조회할지 지정합니다. 기본값은 5년입니다. 필요 시 `--years 2023 2022 ...` 형식으로 명시적으로 지정할 수 있습니다.
- `--fs-div`: 연결재무제표(CFS) 또는 별도재무제표(OFS) 선택. 기본값은 CFS입니다.
- `--output`: 생성될 엑셀 파일 경로. 기본값은 `dart_financials.xlsx`입니다.

스크립트는 각 계정을 행으로, `YYYY_Qn` 형태의 분기별 열(가장 오래된 연도부터 순서대로)을 만들어 모든 금액을 엑셀 시트(`Financials`)에 저장합니다. 분기별 데이터가 없는 경우에도 해당 열은 엑셀에 빈 값으로 유지됩니다.

## 웹 인터페이스 (자연어 검색)

자연어로 회사명과 기간을 입력하면 동일한 데이터를 엑셀로 내려받을 수 있는 Flask 기반 웹 애플리케이션도 제공합니다.

1. 필요한 패키지를 설치하고 환경변수에 DART API 키를 설정합니다.

   ```bash
   export DART_API_KEY="<API_KEY>"
   pip install -r requirements.txt
   ```

2. 개발 서버를 실행합니다.

   ```bash
   FLASK_APP=web.app flask run
   ```

3. 브라우저에서 `http://127.0.0.1:5000/`에 접속하여 "삼성전자 최근 5년 연결 재무제표 보여줘"와 같이 자연어로 검색합니다.

웹 페이지에서는 상위 계정의 추이를 Plotly 그래프로 시각화해 주며, 전체 데이터를 엑셀 파일로 즉시 다운로드할 수 있습니다. 입력창에 API 키를 직접 넣을 수도 있고, `DART_API_KEY` 환경변수를 설정하면 자동으로 사용합니다.

## 동작 방식

1. 회사명으로부터 DART 법인코드를 조회합니다.
2. 지정한 연도/분기에 대해 `fnlttSinglAcntAll` API를 호출하여 모든 계정을 수집합니다.
3. 계정 ID/명/재무제표 구분을 메타데이터로 유지하면서 분기별 값을 피벗합니다.
4. `openpyxl`을 사용해 엑셀 파일로 저장합니다.

> **참고:** 네트워크 연결이 필요하며, 실제 데이터를 내려받으려면 DART API 사용량 제한을 준수해야 합니다.
