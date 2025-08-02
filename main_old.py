import sqlite3
from datetime import datetime
from pathlib import Path
import numpy as np
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re
import whisper
import torch
import io
import tempfile
import ssl
import certifi
import os

# 전역 SSL 설정
ssl._create_default_https_context = ssl._create_unverified_context
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

app = FastAPI()

# CORS 미들웨어 추가
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 오리진 허용 (개발용)
    allow_credentials=True,
    allow_methods=["*"],  # 모든 HTTP 메소드 허용
    allow_headers=["*"],  # 모든 헤더 허용
)
# Whisper 모델 설정
whisper_model = None

# 텍스트 기반 욕설 패턴 감지
PROFANITY_PATTERNS = {
    1: ["씨발", "시발", "병신", "좆", "썅", "개새끼", "씨팔", "시팔"],
    2: ["새끼", "지랄", "엿", "개놈", "개년", "아가리", "ㅗ", "ㅅㅂ", "짜증", "멍청", "멍청이", "바보"],
    3: ["미친", "빡치", "꺼져", "닥쳐", "ㅄ", "ㅆㅂ", "개", "죽어", "등신", "또라이"]
}

# Pydantic 모델
class SensitivityRequest(BaseModel):
    sensitivity: int

sensitivity_level = 2
audio_buffer = []
buffer_size = 24000  # 1.5초 버퍼 (16kHz) - 딜레이 감소
min_audio_length = 8000  # 최소 0.5초 오디오 
sample_rate = 16000
max_buffer_length = 48000  # 최대 3초로 제한하여 메모리 누적 방지

def init_db():
    # 기존 DB 파일 삭제 (스키마 변경을 위해)
    import os
    if os.path.exists("data.db"):
        os.remove("data.db")
        print("🗄️ 기존 DB 삭제됨")
    
    conn = sqlite3.connect("data.db")
    conn.execute("""
        CREATE TABLE detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            pattern TEXT,
            patterns TEXT,
            confidence REAL,
            audio_level REAL,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("🗄️ 새 DB 스키마 생성 완료")

def get_profanity_patterns(sensitivity: int) -> list:
    patterns = []
    for level in range(1, sensitivity + 1):
        level_patterns = PROFANITY_PATTERNS.get(level, [])
        patterns.extend(level_patterns)
        print(f"📋 레벨 {level} 패턴 추가: {level_patterns}")
    print(f"🎯 총 로드된 패턴 ({sensitivity} 레벨): {patterns}")
    return patterns

def safe_json_convert(obj):
    """JSON 직렬화를 위한 안전한 변환"""
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, (np.integer, np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: safe_json_convert(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [safe_json_convert(item) for item in obj]
    return obj

def load_whisper_model():
    """Whisper 모델 로드"""
    global whisper_model
    if whisper_model is None:
        print("🤖 Whisper 모델 로딩 중...")
        try:
            whisper_model = whisper.load_model("base")
            print("✅ Whisper 모델 로드 완료")
        except Exception as e:
            print(f"🔴 모델 로드 실패: {e}")
            return None
    return whisper_model

def preprocess_audio(audio_data: np.ndarray) -> np.ndarray:
    """간단한 오디오 정규화"""
    # 정규화만 수행 (-1 ~ 1 범위)
    if audio_data.max() > 1.0 or audio_data.min() < -1.0:
        audio_data = audio_data / np.max(np.abs(audio_data))
    
    return audio_data.astype(np.float32)

def is_korean_text(text: str) -> bool:
    """텍스트가 한글인지 확인"""
    if not text.strip():
        return False
    
    korean_chars = 0
    total_chars = 0
    
    for char in text:
        if char.strip():  # 공백이 아닌 문자만
            total_chars += 1
            # 한글 유니코드 범위: 가-힣 (완성형), ㄱ-ㅎㅏ-ㅣ (초성/중성/종성)
            if '\uAC00' <= char <= '\uD7A3' or '\u3131' <= char <= '\u318E':
                korean_chars += 1
    
    if total_chars == 0:
        return False
    
    korean_ratio = korean_chars / total_chars
    return korean_ratio >= 0.7  # 70% 이상이 한글이면 한글 텍스트로 판정

async def call_whisper_stt(audio_data: np.ndarray) -> str:
    """Whisper 음성인식 호출"""
    try:
        model = load_whisper_model()
        if model is None:
            return ""
        
        # 오디오 데이터 정규화
        if audio_data.max() > 1.0 or audio_data.min() < -1.0:
            audio_data = audio_data / np.max(np.abs(audio_data))
        
        audio_float32 = audio_data.astype(np.float32)
        
        # 음성인식 실행
        result = model.transcribe(audio_float32)
        text = result.get("text", "").strip()
        print(f"🎤 인식된 텍스트: '{text}'")
        return text
        
    except Exception as e:
        print(f"🔴 Whisper STT 오류: {e}")
        return ""


def detect_profanity_from_text(text: str, patterns: list) -> dict:
    """텍스트에서 욕설 감지"""
    if not text.strip():
        return {"detected": False, "pattern": None, "confidence": 0, "text": text}
    
    text_lower = text.lower()
    detected_patterns = []
    
    for pattern in patterns:
        if pattern.lower() in text_lower:
            detected_patterns.append(pattern)
    
    confidence = 0.8 if detected_patterns else 0
    
    return {
        "detected": len(detected_patterns) > 0,
        "pattern": detected_patterns[0] if detected_patterns else None,
        "patterns": detected_patterns,
        "confidence": confidence,
        "text": text
    }

@app.on_event("startup")
async def startup():
    init_db()
    load_whisper_model()  # 서버 시작 시 모델 미리 로드
    print("🚀 Whisper 기반 실시간 욕설 감지 서버 시작")
    print(f"📊 감지 레벨: {sensitivity_level} (1=강, 2=중, 3=약)")
    print("🎯 실시간 오디오 스트림 대기 중")

@app.get("/")
async def root():
    return {"message": "실시간 욕설 감지 서버"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🟢 클라이언트 연결 - Whisper STT 스트림 시작")
    
    global audio_buffer
    audio_buffer = []
    patterns = get_profanity_patterns(sensitivity_level)
    
    try:
        while True:
            audio_data = await websocket.receive_bytes()
            
            # 바이트를 NumPy 배열로 변환
            audio_chunk = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            
            # 버퍼에 추가
            audio_buffer.extend(audio_chunk)
            
            # 버퍼 크기 제한 (메모리 누적 방지)
            if len(audio_buffer) > max_buffer_length:
                audio_buffer = audio_buffer[-max_buffer_length:]
                print("⚠️ 버퍼 크기 제한으로 오래된 데이터 제거")
            
            # 오디오 에너지 계산 (음성 활동 감지용)
            energy = np.mean(np.abs(audio_chunk)) if len(audio_chunk) > 0 else 0
            
            # 버퍼가 충분하면 STT 분석
            if len(audio_buffer) >= buffer_size:
                # 분석할 청크 추출
                chunk_to_analyze = np.array(audio_buffer[:buffer_size])
                audio_buffer = audio_buffer[buffer_size//2:]  # 50% 오버랩으로 조정
                
                # 음성 활동이 있는지 확인
                chunk_energy = np.mean(np.abs(chunk_to_analyze))
                
                if chunk_energy > 0.02:  # 더 높은 임계값으로 불필요한 STT 호출 감소
                    print(f"🔊 음성 활동 감지 (에너지: {chunk_energy:.3f}) - STT 실행 중...")
                    
                    # Whisper STT 호출
                    recognized_text = await call_whisper_stt(chunk_to_analyze)
                    
                    if recognized_text:
                        # 텍스트에서 욕설 감지
                        result = detect_profanity_from_text(recognized_text, patterns)
                        
                        if result["detected"]:
                            # DB 저장
                            conn = sqlite3.connect("data.db")
                            conn.execute(
                                "INSERT INTO detections (text, pattern, patterns, confidence, audio_level, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                                (recognized_text, result["pattern"], ",".join(result["patterns"]), float(result["confidence"]), float(chunk_energy), datetime.now().isoformat())
                            )
                            conn.commit()
                            conn.close()
                            
                            print(f"🔴 욕설 감지! 텍스트: '{recognized_text}' 패턴: {result['patterns']} 신뢰도: {result['confidence']:.2f}")
                            
                            response_data = {
                                "detected": True,
                                "text": recognized_text,
                                "pattern": result["pattern"],
                                "patterns": result["patterns"],
                                "confidence": result["confidence"],
                                "energy": chunk_energy,
                                "timestamp": datetime.now().isoformat()
                            }
                            await websocket.send_json(safe_json_convert(response_data))
                        else:
                            print(f"🟢 정상 텍스트: '{recognized_text}'")
                            response_data = {
                                "detected": False,
                                "text": recognized_text,
                                "energy": chunk_energy,
                                "timestamp": datetime.now().isoformat()
                            }
                            await websocket.send_json(safe_json_convert(response_data))
                    else:
                        # STT 결과가 없음 (무음 또는 인식 실패)
                        response_data = {
                            "detected": False,
                            "text": "",
                            "energy": chunk_energy,
                            "message": "음성 인식 실패 또는 무음"
                        }
                        await websocket.send_json(safe_json_convert(response_data))
                else:
                    # 음성 활동 없음
                    response_data = {
                        "detected": False,
                        "text": "",
                        "energy": chunk_energy,
                        "message": "음성 활동 없음"
                    }
                    await websocket.send_json(safe_json_convert(response_data))
                    
    except WebSocketDisconnect:
        print("🔴 클라이언트 연결 끊김")

@app.post("/sensitivity")
async def set_sensitivity(request: SensitivityRequest):
    global sensitivity_level
    if request.sensitivity not in [1, 2, 3]:
        return {"error": "Sensitivity must be 1, 2, or 3"}
    old_level = sensitivity_level
    sensitivity_level = request.sensitivity
    level_names = {1: "강", 2: "중", 3: "약"}
    print(f"⚙️ 감지 레벨 변경: {level_names[old_level]} → {level_names[request.sensitivity]}")
    return {"sensitivity": request.sensitivity}

@app.get("/sensitivity")
async def get_sensitivity():
    return {"sensitivity": sensitivity_level}

@app.get("/stats")
async def get_stats():
    conn = sqlite3.connect("data.db")
    cursor = conn.execute(
        "SELECT COUNT(*), AVG(confidence) FROM detections WHERE date(timestamp) = date('now')"
    )
    today_count, today_avg = cursor.fetchone()
    
    cursor = conn.execute(
        "SELECT COUNT(*), AVG(confidence) FROM detections WHERE date(timestamp) >= date('now', '-7 days')"
    )
    week_count, week_avg = cursor.fetchone()
    
    conn.close()
    
    return {
        "today": {"count": today_count or 0, "avg_confidence": round(today_avg or 0, 2)},
        "week": {"count": week_count or 0, "avg_confidence": round(week_avg or 0, 2)}
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)