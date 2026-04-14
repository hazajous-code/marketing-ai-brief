"""LLM-powered key point extraction for Marketing AI Brief.

Uses Ollama (Qwen2.5) to extract 2-3 actionable key points per article.
Falls back to keyword-matched templates when the LLM is unavailable.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, List

from ollama_client import ollama_generate

MAX_CHARS = 1500

PROMPT = """
You are a senior marketing strategist. Read the article and extract 2-3 key points.

Rules:
- Each bullet is one concise sentence (15 words max)
- Focus on what matters to a CMO: strategic shifts, competitive impact, action triggers
- No filler, no obvious facts, no source attribution
- Marketing lens: brand, performance, platform, AI/data, commerce
- Write in the same language as the article

Output format (strict — nothing else):
- bullet one
- bullet two
- bullet three
""".strip()

_WS = re.compile(r"\s+")

_TEMPLATES = [
    {
        "keywords": ("search", "seo", "google", "bing", "ranking", "serp", "geo"),
        "bullets": [
            "검색 노출 경쟁 축이 키워드 순위에서 AI 인용 가능성으로 이동",
            "콘텐츠 설계 기준이 CTR에서 citability 중심으로 전환 필요",
        ],
    },
    {
        "keywords": ("automation", "campaign", "ad", "creative", "bidding", "targeting", "pmax"),
        "bullets": [
            "캠페인 자동화가 셋업-크리에이티브-입찰 전 영역으로 확대",
            "차별화는 자동화 범위가 아니라 학습 루프 설계 품질에서 결정",
        ],
    },
    {
        "keywords": ("commerce", "retail", "d2c", "dtc", "shopping", "purchase"),
        "bullets": [
            "구매 여정이 검색 중심에서 AI 추천·숏폼 발견 중심으로 이동",
            "전통적 퍼널 모델의 유효성 하락, 플랫폼별 경로 재설계 필요",
        ],
    },
    {
        "keywords": ("llm", "generative", "gpt", "chatbot", "agent", "agentic", "model"),
        "bullets": [
            "LLM이 마케팅 실행을 넘어 전략 수립까지 침투 중",
            "마케팅 팀 역할이 '실행'에서 '설계+감독'으로 전환 압력",
        ],
    },
    {
        "keywords": ("data", "privacy", "cookie", "tracking", "measurement", "attribution"),
        "bullets": [
            "서드파티 데이터 축소로 기존 측정·어트리뷰션 체계 신뢰도 하락",
            "퍼스트파티 데이터 인프라 구축과 증분 테스트 체계 전환 시급",
        ],
    },
    {
        "keywords": ("platform", "meta", "tiktok", "youtube", "instagram", "algorithm"),
        "bullets": [
            "플랫폼 알고리즘 변화가 도달 효율과 콘텐츠 전략을 동시에 재편",
            "특정 채널 의존도 관리가 핵심 리스크 과제로 부상",
        ],
    },
    {
        "keywords": ("brand", "branding", "positioning", "awareness", "perception"),
        "bullets": [
            "퍼포먼스 효율 한계가 드러나며 브랜드 투자 ROI 재조명",
            "단기 전환 최적화와 장기 브랜드 자산 구축 간 예산 비율 재설정 필요",
        ],
    },
]

_DEFAULT_BULLETS = [
    "시장 변화 속도가 빨라지며 전략 대응 주기 단축 필요",
    "변화 신호 조기 포착 후 실험 기반 의사결정 루프 가동 권장",
]


def _clean(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip()


def _truncate(text: str) -> str:
    return text[:MAX_CHARS] if len(text) > MAX_CHARS else text


def _extract_lead(text: str, max_len: int = 80) -> str:
    sentences = re.split(r"[.!?。]\s+", text)
    lead = sentences[0] if sentences else text
    if len(lead) > max_len:
        lead = lead[:max_len].rsplit(" ", 1)[0]
    return lead.strip(".!? ")


def _pick_bullets(text: str) -> list[str]:
    lower = text.lower()
    best, best_score = _DEFAULT_BULLETS, 0
    for tpl in _TEMPLATES:
        score = sum(1 for kw in tpl["keywords"] if kw in lower)
        if score > best_score:
            best, best_score = tpl["bullets"], score
    return best


def _fallback(text: str) -> str:
    cleaned = _clean(text)
    if not cleaned:
        return "- 분석에 필요한 충분한 콘텐츠가 없습니다"

    lead = _extract_lead(cleaned)
    bullets = _pick_bullets(cleaned)
    lines = [f"- {lead}"] + [f"- {b}" for b in bullets]
    return "\n".join(lines[:3])


def _parse_bullets(raw: str) -> str:
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip().startswith("-")]
    if len(lines) < 2:
        return ""
    return "\n".join(lines[:3])


@lru_cache(maxsize=1024)
def summarize_text(text: str, length: str = "medium") -> str:
    cleaned = _clean(text)
    if not cleaned:
        return _fallback(cleaned)
    try:
        prompt = f"{PROMPT}\n\nArticle:\n{_truncate(cleaned)}"
        raw = ollama_generate(prompt, timeout=45, retries=1)
        parsed = _parse_bullets(raw)
        if parsed:
            return parsed
    except Exception:
        pass
    return _fallback(cleaned)
