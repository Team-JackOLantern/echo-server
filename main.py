import sqlite3
from datetime import datetime
from pathlib import Path
import numpy as np
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import re

app = FastAPI()

# 실시간 키워드 감지용 욕설 패턴
PROFANITY_PATTERNS = {
    1: ["씨발", "시발", "병신", "좆", "썅", "개새끼"],
    2: ["새끼", "지랄", "엿", "개놈", "개년", "아가리", "ㅗ", "ㅅㅂ"],
    3: ["미친", "빡치", "짜증", "꺼져", "닥쳐", "바보", "멍청", "ㅄ", "ㅆㅂ"]
}

sensitivity_level = 2
audio_buffer = []
buffer_size = 4000  # 0.25초 버퍼 (16kHz)

def init_db():
    conn = sqlite3.connect("data.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY,
            pattern TEXT,
            confidence REAL,
            audio_level REAL,
            timestamp TEXT
        )
    """)
    conn.close()

def get_profanity_patterns(sensitivity: int) -> list:
    patterns = []
    for level in range(1, sensitivity + 1):
        patterns.extend(PROFANITY_PATTERNS.get(level, []))
    return patterns

def detect_profanity_realtime(audio_chunk: np.ndarray, patterns: list) -> dict:
    # 오디오 에너지 계산
    energy = np.mean(np.abs(audio_chunk))
    rms = np.sqrt(np.mean(audio_chunk**2))
    
    # 간단한 음성 활동 감지
    if energy < 0.01:
        return {"detected": False, "pattern": None, "confidence": 0, "energy": energy}
    
    # 주파수 분석 (FFT)
    fft = np.fft.fft(audio_chunk)
    freqs = np.fft.fftfreq(len(audio_chunk), 1/16000)
    magnitude = np.abs(fft)
    
    # 욕설 특성 감지 (간단한 휴리스틱)
    # 1. 높은 에너지 + 급격한 변화
    # 2. 특정 주파수 대역 강조
    high_freq_energy = np.mean(magnitude[freqs > 2000])
    mid_freq_energy = np.mean(magnitude[(freqs > 500) & (freqs < 2000)])
    
    # 욕설 패턴 점수 계산
    profanity_score = 0
    if rms > 0.1:  # 충분한 음성 에너지
        profanity_score += 0.3
    if high_freq_energy > mid_freq_energy * 1.5:  # 고주파 강조
        profanity_score += 0.4
    if energy > 0.05:  # 강한 음성
        profanity_score += 0.3
    
    detected = profanity_score > 0.7
    pattern = "audio_pattern" if detected else None
    
    return {
        "detected": detected,
        "pattern": pattern,
        "confidence": profanity_score,
        "energy": energy,
        "rms": rms
    }

@app.on_event("startup")
async def startup():
    init_db()
    print("🚀 실시간 욕설 감지 서버 시작")
    print(f"📊 감지 레벨: {sensitivity_level} (1=강, 2=중, 3=약)")
    print("🎯 실시간 오디오 스트림 대기 중")

@app.get("/")
async def root():
    return {"message": "실시간 욕설 감지 서버"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🟢 클라이언트 연결 - 실시간 스트림 시작")
    
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
            
            # 버퍼가 충분하면 분석
            if len(audio_buffer) >= buffer_size:
                # 분석할 청크 추출
                chunk_to_analyze = np.array(audio_buffer[:buffer_size])
                audio_buffer = audio_buffer[buffer_size//2:]  # 50% 오버랩
                
                # 실시간 욕설 감지
                result = detect_profanity_realtime(chunk_to_analyze, patterns)
                
                print(f"🔊 에너지:{result['energy']:.3f} RMS:{result['rms']:.3f} 점수:{result['confidence']:.2f}")
                
                if result["detected"]:
                    # DB 저장
                    conn = sqlite3.connect("data.db")
                    conn.execute(
                        "INSERT INTO detections (pattern, confidence, audio_level, timestamp) VALUES (?, ?, ?, ?)",
                        (result["pattern"], result["confidence"], result["energy"], datetime.now().isoformat())
                    )
                    conn.commit()
                    conn.close()
                    
                    print(f"🔴 욕설 감지! 신뢰도: {result['confidence']:.2f}")
                    
                    await websocket.send_json({
                        "detected": True,
                        "pattern": result["pattern"],
                        "confidence": result["confidence"],
                        "energy": result["energy"],
                        "timestamp": datetime.now().isoformat()
                    })
                else:
                    await websocket.send_json({
                        "detected": False,
                        "energy": result["energy"],
                        "rms": result["rms"]
                    })
                    
    except WebSocketDisconnect:
        print("🔴 클라이언트 연결 끊김")

@app.post("/sensitivity")
async def set_sensitivity(sensitivity: int):
    global sensitivity_level
    if sensitivity not in [1, 2, 3]:
        return {"error": "Sensitivity must be 1, 2, or 3"}
    old_level = sensitivity_level
    sensitivity_level = sensitivity
    level_names = {1: "강", 2: "중", 3: "약"}
    print(f"⚙️ 감지 레벨 변경: {level_names[old_level]} → {level_names[sensitivity]}")
    return {"sensitivity": sensitivity}

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