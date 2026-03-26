# Wise Report Telegram Bot

와이즈리포트의 당일 발간 리포트를 `기업`, `산업`, `정기`로 나눠서 텔레그램으로 보내는 파이썬 스크립트입니다.

정렬은 서버 정렬과 별개로 파이썬에서 한 번 더 수행해서 `ㄱㄴㄷ` 오름차순으로 맞춥니다.

## 설치

```bash
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 환경변수

`.env.example`을 참고해서 `.env` 파일을 만들거나, 시스템 환경변수에 아래 값을 넣어 주세요.

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 실행

오늘 날짜 기준으로 텔레그램 전송:

```bash
py -3 wise_report_bot.py
```

전송 없이 콘솔에서 확인:

```bash
py -3 wise_report_bot.py --dry-run
```

특정 날짜 테스트:

```bash
py -3 wise_report_bot.py --date 2026-03-24 --dry-run
```

## 동작 방식

1. 서울 시간 기준 오늘 날짜를 계산합니다.
2. `기업(fmt=1)`, `산업(fmt=2)`, `정기(fmt=3)` URL을 각각 조회합니다.
3. HTML 테이블에서 행을 파싱합니다.
4. `기업명`, `산업명`, `기관명` 기준으로 `ㄱㄴㄷ` 오름차순 정렬합니다.
5. 텔레그램 4096자 제한에 맞춰 메시지를 자동 분할해서 보냅니다.
6. `--skip-non-business-day` 옵션을 쓰면 한국 주말과 공휴일에는 전송하지 않고 정상 종료합니다.

## GitHub Actions 자동 실행

`.github/workflows/wise-report.yml`이 포함되어 있어서 GitHub에 올리면 자동 실행할 수 있습니다.

```bash
python wise_report_bot.py --skip-non-business-day
```

### 스케줄

- GitHub Actions의 cron은 `UTC` 기준입니다.
- 현재 워크플로우는 아래 두 개의 cron으로 설정되어 있습니다.
- `10,50 22 * * 0-4` -> 한국 시간 기준 `월~금 07:10, 07:50`
- `5,30 23 * * 0-4` -> 한국 시간 기준 `월~금 08:05, 08:30`
- 위 시간마다 같은 날짜 기준 리포트를 각각 한 번씩 전송합니다.
- 한국 공휴일은 스크립트가 감지해서 전송을 건너뜁니다.

### GitHub Secrets 설정

저장소 `Settings > Secrets and variables > Actions`에서 아래 두 개를 추가해 주세요.

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 수동 실행

GitHub 저장소의 `Actions > Wise Report Bot > Run workflow`에서 바로 테스트 실행할 수 있습니다.
