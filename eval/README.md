# STT 엔진 평가 (PoC 게이트)

00-decisions 5장의 PoC 게이트(STT 엔진 최종 선택)를 판정하는 평가 도구다. 후보는 A) faster-whisper large-v3 + pyannote,
B) Qwen3-ASR-1.7B + pyannote이다. 판정은 **용어 정확도 우선**이고, 동률이면 A를 택한다.
처리 시간 기준은 **1시간 회의를 업로드 완료부터 자동 요약 저장까지 15분 이내**다.

## 음원 보관 (00-decisions 4장)

- 음원은 git에 넣지 않는다. 목소리도 개인정보이고, 1시간 음원은 GitHub 파일 크기 제한을 넘는다. 평가를 돌리는 서버의 디스크(`--audio-dir`)에 둔다.
- 레포에는 `samples/manifest.json`(메타데이터와 음원 SHA256)과 정답 전사(`samples/*.ref.json`)만 커밋한다.
- 평가 도구는 실행 전에 모든 음원이 있고 SHA256이 맞는지 확인한다. 하나라도 다르면 아무것도 돌리지 않고 멈춘다.
- 실제 회의 녹음은 쓰지 않는다. 비민감 회의 음성 10건 이상(2~5인, 겹침 발화와 용어 포함)을 따로 준비한다.

## manifest (`samples/manifest.json`)

```json
{
  "version": 1,
  "samples": [
    {
      "id": "meeting-01",
      "audio": "meeting-01.wav",
      "sha256": "<sha256sum 결과>",
      "duration_sec": 612.4,
      "speakers": 3,
      "language": "ko-KR",
      "reference": "meeting-01.ref.json",
      "keywords": ["엘라스틱서치", "벡터DB"],
      "terms": ["엘라스틱서치", "벡터DB", "쿠버네티스"],
      "corrections": [["엘라스틱 서치", "엘라스틱서치"]],
      "notes": "4인 기술 회의, 겹침 발화 있음"
    }
  ]
}
```

| 필드 | 뜻 |
|---|---|
| `id` | 샘플 이름(영문·숫자·`.`·`_`·`-`) |
| `audio` | `--audio-dir` 안의 파일 이름. 포맷은 무엇이든 된다(ffmpeg로 16kHz mono로 바꾼다) |
| `sha256` | 음원의 SHA256(`sha256sum <파일>`) |
| `duration_sec`, `speakers` | 길이(초)와 실제 화자 수 |
| `language` | 엔진에 넘기는 언어(앱과 같은 `ko-KR` 형식) |
| `reference` | 정답 전사 파일 이름(`samples/` 안) |
| `keywords` | 엔진 힌트로 주는 도메인 키워드(앱의 `domain_keywords`에 해당). 선택 |
| `terms` | 용어 적중률을 셀 용어. 선택 |
| `corrections` | 사전 치환 `[오인식 표현, 정식 표기]`(앱의 `term_corrections`에 해당). accuracy 모드만 쓴다. 선택 |

## 정답 전사 (`samples/<id>.ref.json`)

```json
{
  "segments": [
    {"speaker": "A", "start": 0.0, "end": 4.2, "text": "오늘은 검색엔진 개편을 논의하겠습니다."},
    {"speaker": "B", "start": 4.6, "end": 7.9, "text": "네, 일정부터 정하죠."}
  ]
}
```

- `start`·`end`는 초, `speaker`는 아무 이름이나 쓴다. 엔진의 화자와는 최적 매핑으로 맞춘다.
- 사람이 들으며 검수한 전사여야 한다. 엔진 출력을 고친 것이라도 끝까지 들으며 확인한다.
- 숫자·영문 용어는 회의록에 쓰는 표기로 적는다(예: `3개`, `벡터DB`). 엔진과 표기가 다르면 그만큼 CER에 들어간다.
- 겹친 발화는 화자별 세그먼트로 따로 적고 시간을 겹치게 둔다. CER은 시작 시각 순으로 이어 붙인 글로 잰다.

## 샘플 추가

```bash
sha256sum meeting-01.wav                       # manifest의 sha256
ffprobe -v error -show_entries format=duration -of csv=p=0 meeting-01.wav   # duration_sec
# samples/meeting-01.ref.json을 쓰고 manifest에 항목을 더한 뒤
server/.venv/bin/python eval/run_eval.py check --audio-dir /data/eval-audio
```

## 실행

레포 루트에서 airgap 엔진(`server/requirements-airgap.txt`)이 설치된 Python으로 돌린다. `server/.env`는 읽지 않는다.

- **accuracy**: 엔진을 워커와 같은 흐름(`server/pipeline/recognize.py`)으로 이 프로세스에서 돌린다. DB·워커·요약은 쓰지 않는다. 엔진을 비교할 때 쓴다.
  ```bash
  WHISPER_MODEL_PATH=/models/faster-whisper-large-v3 PYANNOTE_PIPELINE_PATH=/models/speaker-diarization-community-1 \
  STT_DEVICE=cuda server/.venv/bin/python eval/run_eval.py accuracy --audio-dir /data/eval-audio --stt faster_whisper
  ```
  첫 샘플의 stt·diarize 시간에는 모델을 올리는 시간이 들어간다. `--correction-llm`이면 LLM 용어 보정도 돌린다(`LLM_*` env).
- **e2e**: 배포한 시스템의 API로 업로드 → 전사 → 자동 요약 저장까지 잰다. 처리 시간 기준은 이 결과로 판정한다.
  ```bash
  EVAL_EMAIL=... EVAL_PASSWORD=... server/.venv/bin/python eval/run_eval.py e2e --audio-dir /data/eval-audio \
    --url https://<서버> [--insecure]
  ```
  - 샘플의 `keywords`는 임시 도메인으로 넘기고 끝나면 지운다. 사전 치환(`corrections`)은 넘기지 않는다.
  - 업로드한 회의는 끝나면 지운다(`--keep`이면 남긴다).
- 결과: 표를 출력하고 `eval/results/<시각>-<모드>.json`(git 밖)에 설정, 지표, 단계별 시간, 인식 결과를 남긴다.
  한 샘플이 실패해도 나머지를 돌리고, 실패가 있으면 종료 코드 1이다.

## 지표

| 지표 | 계산 |
|---|---|
| CER | 정답·인식 결과를 NFC·소문자로 바꾸고 공백·문장부호를 뺀 뒤, 글자 편집 거리 ÷ 정답 글자 수. 합계는 샘플의 오류 수 합 ÷ 정답 글자 수 합 |
| 용어 적중률 | 용어마다 min(정답에 나온 횟수, 인식에 나온 횟수)의 합 ÷ 정답에 나온 횟수의 합. 띄어쓰기는 무시한다. 정답보다 많이 나온 횟수는 `extra`로 따로 남긴다 |
| DER | pyannote.metrics `DiarizationErrorRate(collar=0.5, skip_overlap=False)`. 경계 앞뒤 0.25초는 뺀다. 놓침·오탐·화자 혼동을 함께 남긴다 |
| 처리 시간 | accuracy는 단계별(normalize, stt, diarize, merge, glossary, [correct]), e2e는 업로드 완료부터 전사 완료·요약 저장까지. 음원 1시간당 분으로도 보여 준다 |
