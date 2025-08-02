import sqlite3
import asyncio
import numpy as np
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models.database import Database
from models.user import User
from services.whisper_service import WhisperService
from services.profanity_service import ProfanityService
from utils.helpers import safe_json_convert

# FastAPI 앱 생성
app = FastAPI(title="실시간 욕설 감지 서버")

# CORS 미들웨어
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 전역 변수
db = Database()
whisper_service = WhisperService()
profanity_service = ProfanityService()

# 오디오 버퍼 설정
audio_buffer = []
buffer_size = 24000  # 1.5초 버퍼
max_buffer_length = 48000  # 최대 3초

# Pydantic 모델들
class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class SensitivityRequest(BaseModel):
    sensitivity: int

# 인증 관련 엔드포인트
@app.post("/auth/register")
async def register(user_data: UserRegister):
    """사용자 등록"""
    conn = db.get_connection()
    user_model = User(conn)
    
    result = user_model.create_user(user_data.username, user_data.password)
    conn.close()
    
    if result["success"]:
        return {"user_id": result["user_id"], "message": result["message"]}
    else:
        raise HTTPException(status_code=400, detail=result["message"])

@app.post("/auth/login")
async def login(user_data: UserLogin):
    """사용자 로그인"""
    conn = db.get_connection()
    user_model = User(conn)
    
    result = user_model.authenticate(user_data.username, user_data.password)
    conn.close()
    
    if result["success"]:
        return {"user_id": result["user_id"], "message": result["message"]}
    else:
        raise HTTPException(status_code=401, detail=result["message"])

# 사용자 검증 함수
def verify_user(user_id: str) -> bool:
    """사용자 ID 검증"""
    conn = db.get_connection()
    user_model = User(conn)
    user = user_model.get_user_by_id(user_id)
    conn.close()
    return user is not None

# 기본 엔드포인트
@app.get("/")
async def root():
    return {"message": "실시간 욕설 감지 서버 (모듈화 버전)"}

# WebSocket 엔드포인트
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # WebSocket에서는 URL 파라미터로 user_id를 받음
    query_params = websocket.query_params
    user_id = query_params.get("user_id")
    
    # 사용자 인증
    if not user_id or not verify_user(user_id):
        await websocket.send_json({"error": "인증되지 않은 사용자입니다"})
        await websocket.close()
        return
    
    print(f"🟢 사용자 {user_id} 연결 - Whisper STT 스트림 시작")
    
    global audio_buffer
    audio_buffer = []
    
    try:
        while True:
            audio_data = await websocket.receive_bytes()
            
            # 바이트를 NumPy 배열로 변환
            audio_chunk = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            
            # 버퍼에 추가
            audio_buffer.extend(audio_chunk)
            
            # 버퍼 크기 제한
            if len(audio_buffer) > max_buffer_length:
                audio_buffer = audio_buffer[-max_buffer_length:]
            
            # 오디오 에너지 계산
            energy = np.mean(np.abs(audio_chunk)) if len(audio_chunk) > 0 else 0
            
            # 버퍼가 충분하면 STT 분석
            if len(audio_buffer) >= buffer_size:
                chunk_to_analyze = np.array(audio_buffer[:buffer_size])
                audio_buffer = audio_buffer[buffer_size//2:]  # 50% 오버랩
                
                chunk_energy = np.mean(np.abs(chunk_to_analyze))
                
                if chunk_energy > 0.02:  # 음성 활동 감지
                    print(f"🔊 음성 활동 감지 (에너지: {chunk_energy:.3f}) - STT 실행 중...")
                    
                    # Whisper STT 호출
                    recognized_text = await whisper_service.transcribe(chunk_to_analyze)
                    
                    if recognized_text:
                        # 욕설 감지
                        result = profanity_service.detect(recognized_text)
                        
                        if result["detected"]:
                            # DB 저장
                            conn = db.get_connection()
                            conn.execute(
                                "INSERT INTO detections (user_id, text, pattern, patterns, confidence, audio_level, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (user_id, recognized_text, result["pattern"], ",".join(result["patterns"]), 
                                 float(result["confidence"]), float(chunk_energy), datetime.now().isoformat())
                            )
                            conn.commit()
                            conn.close()
                            
                            print(f"🔴 욕설 감지! 사용자: {user_id}, 텍스트: '{recognized_text}'")
                            
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
                            response_data = {
                                "detected": False,
                                "text": recognized_text,
                                "energy": chunk_energy,
                                "timestamp": datetime.now().isoformat()
                            }
                            await websocket.send_json(safe_json_convert(response_data))
                    else:
                        # STT 결과가 없음
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
        print(f"🔴 사용자 {user_id} 연결 끊김")

# 감지 레벨 설정
@app.post("/sensitivity")
async def set_sensitivity(request: SensitivityRequest, user_id: Optional[str] = Header(None)):
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    if profanity_service.set_sensitivity(request.sensitivity):
        return {"sensitivity": request.sensitivity, "message": "감지 레벨 변경 완료"}
    else:
        raise HTTPException(status_code=400, detail="잘못된 감지 레벨입니다 (1, 2, 3만 가능)")

@app.get("/sensitivity")
async def get_sensitivity(user_id: Optional[str] = Header(None)):
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    return {"sensitivity": profanity_service.sensitivity_level}

# 통계 조회
@app.get("/stats")
async def get_stats(user_id: Optional[str] = Header(None)):
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    
    # 오늘 통계
    cursor = conn.execute(
        "SELECT COUNT(*), AVG(confidence) FROM detections WHERE user_id = ? AND date(timestamp) = date('now')",
        (user_id,)
    )
    today_count, today_avg = cursor.fetchone()
    
    # 일주일 통계
    cursor = conn.execute(
        "SELECT COUNT(*), AVG(confidence) FROM detections WHERE user_id = ? AND date(timestamp) >= date('now', '-7 days')",
        (user_id,)
    )
    week_count, week_avg = cursor.fetchone()
    
    conn.close()
    
    return {
        "today": {"count": today_count or 0, "avg_confidence": round(today_avg or 0, 2)},
        "week": {"count": week_count or 0, "avg_confidence": round(week_avg or 0, 2)}
    }

if __name__ == "__main__":
    import uvicorn
    print("🚀 모듈화된 실시간 욕설 감지 서버 시작")
    uvicorn.run(app, host="0.0.0.0", port=8000)