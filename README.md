# Marketing AI Brief

마케팅 전문가를 위한 AI 뉴스레터. RSS 기반 뉴스 수집, Ollama(LLaMA 3.2) 인사이트 분석, 한/영 번역을 Streamlit 앱으로 제공합니다.

## 구조

```
├── app.py              # Streamlit 뉴스레터 UI
├── collect_news.py     # RSS 수집 + 관련성 스코어링 + 중복 제거
├── summarize.py        # Ollama 기반 마케팅 인사이트 분석
├── translate.py        # 번역 레이어 (Google Translate)
└── requirements.txt
```

## 화면 구성

1. **Masthead** — 날짜와 브리프 제목
2. **Daily Digest** — 3개 고정 카테고리별 심층 인사이트
   - Generative Engine Optimization
   - AI Automation in Marketing Execution
   - Marketing AI Trend
3. **Article List** — 개별 기사 + LLM 분석 (Key Points / Marketing Insight / Strategic Implication)
4. **검색** — 키워드 기반 필터링
5. **더보기** — 9개씩 추가 로드

## 실행

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. Ollama 실행 (LLM 분석용)
ollama run llama3.2

# 3. 앱 실행
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

## 외부 접속 (ngrok / Cloudflare Tunnel)

```bash
# ngrok
ngrok http 8501

# Cloudflare Tunnel
cloudflared tunnel --url http://localhost:8501
```

## 설정 (사이드바)

| 항목 | 설명 |
|------|------|
| RSS feeds | 수집할 RSS URL (줄바꿈 구분) |
| Language | Original / Korean |
| Analysis depth | short / medium / long |
| Max articles | 수집 기사 수 (12~90) |

## 기술 스택

- **Streamlit** — UI
- **feedparser** — RSS 파싱
- **Ollama (LLaMA 3.2)** — 인사이트 분석 / Daily Digest 생성
- **deep-translator** — Google Translate 연동
- **lru_cache / st.cache_data** — 성능 최적화
