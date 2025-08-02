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
from models.group import Group
from services.whisper_service import WhisperService
from services.profanity_service import ProfanityService
from utils.helpers import safe_json_convert

# FastAPI 앱 생성
app = FastAPI(
    title="🎤 실시간 욕설 감지 API",
    description="""
    ## 실시간 음성 욕설 감지 및 그룹 관리 서비스

    이 API는 음성을 실시간으로 분석하여 욕설을 감지하고, 
    사용자들이 그룹을 만들어 함께 욕설 사용을 줄여나갈 수 있는 서비스입니다.

    ### 🚀 주요 기능
    - **실시간 음성 감지**: WebSocket을 통한 실시간 음성 분석
    - **개인 통계**: 시간대별, 기간별 욕설 사용 통계
    - **그룹 시스템**: 친구들과 함께하는 욕설 줄이기 챌린지
    - **순위 시스템**: 그룹 내 욕설 사용 랭킹

    ### 🔄 전체 앱 사용 플로우

    #### 📱 앱 첫 실행 시
    ```
    1. POST /auth/register (회원가입)
       → user_id 획득 및 저장
    
    2. POST /auth/login (로그인)
       → user_id 검증 및 앱 진입
    ```

    #### 🎤 메인 화면 - 실시간 욕설 감지
    ```
    1. POST /recording/on
       Headers: user-id: {user_id}
       → 녹음 시작 준비
    
    2. WebSocket 연결
       ws://localhost:8000/ws?user_id={user_id}
       → 실시간 음성 데이터 송수신
    
    3. 실시간 결과 처리
       - 욕설 감지 시: UI 알림 + 카운트 증가
       - 정상 음성: 로그만 기록
    
    4. POST /recording/off
       → 녹음 중지 및 리소스 정리
    ```

    #### 📊 통계 화면 - 개인 분석
    ```
    1. GET /stats/detailed?period=today
       → 오늘 시간대별 욕설 사용 패턴
    
    2. GET /stats/words
       → 가장 많이 사용한 욕설 단어 순위
    
    3. GET /detections?limit=20
       → 최근 욕설 감지 상세 기록
    ```

    #### 👥 그룹 화면 - 친구들과 챌린지
    ```
    1. POST /groups/create
       Body: {"name": "우리팀", "bet_deadline": "2024-12-31"}
       → 그룹 생성 및 초대 코드 획득
    
    2. POST /groups/join
       Body: {"invite_code": "ABC123"}
       → 친구 그룹에 참여
    
    3. GET /groups/my
       → 내가 참여한 모든 그룹 목록
    
    4. GET /groups/{group_id}/ranking?period=week
       → 그룹 내 욕설 사용 순위 확인
    
    5. POST /groups/banned-words
       Body: {"group_id": 1, "word": "바보"}
       → 그룹 전용 금지어 추가 (관리자만)
    ```

    #### ⚙️ 설정 화면
    ```
    1. GET /sensitivity
       → 현재 감지 레벨 확인
    
    2. POST /sensitivity
       Body: {"sensitivity": 3}
       → 감지 레벨 변경 (1=강, 2=중, 3=약)
    ```

    ### 🔐 인증 방법
    모든 API 요청 시 헤더에 `user-id`를 포함해야 합니다.
    
    ```javascript
    // 예시: JavaScript fetch 사용
    fetch('/stats/detailed', {
        headers: {
            'user-id': 'abc12345'  // 로그인 시 받은 user_id
        }
    })
    ```

    ### 📱 화면별 API 구성
    - **🔐 인증**: 회원가입/로그인
    - **📱 메인 화면**: 실시간 녹음 on/off
    - **📊 통계 화면**: 개인 욕설 사용 분석
    - **👥 그룹 화면**: 그룹 생성/참여/순위
    - **⚙️ 설정**: 감지 레벨 조정

    ### 💡 개발 팁
    - WebSocket 연결은 `/recording/on` 호출 후에 시작
    - 모든 API 응답에는 `user-id` 헤더 필수
    - 에러 핸들링을 위해 HTTP 상태 코드 확인
    - 실시간 성능을 위해 WebSocket 연결 유지 권장

    """,
    version="2.0.0",
    contact={
        "name": "개발팀",
        "email": "support@example.com",
    },
    license_info={
        "name": "MIT License",
    },
)

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

class GroupCreate(BaseModel):
    name: str
    bet_deadline_date: Optional[str] = None  # YYYY-MM-DD 형식
    bet_deadline_time: Optional[str] = None  # HH:MM 형식

class GroupJoin(BaseModel):
    invite_code: str

class BannedWordAdd(BaseModel):
    group_id: int
    word: str

# 인증 관련 엔드포인트
@app.post("/auth/register", 
          tags=["🔐 사용자 인증"], 
          summary="회원가입",
          description="""
          새로운 계정을 생성합니다.
          
          **주의사항:**
          - 사용자명은 고유해야 합니다
          - 비밀번호는 평문으로 저장됩니다 (데모용)
          - 성공 시 8자리 고유 `user_id`가 반환됩니다
          
          **반환되는 user_id를 모든 API 요청의 헤더에 포함하세요!**
          """)
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

@app.post("/auth/login", 
          tags=["🔐 사용자 인증"], 
          summary="로그인",
          description="""
          기존 계정으로 로그인합니다.
          
          **사용법:**
          1. 회원가입 시 사용한 사용자명과 비밀번호 입력
          2. 성공 시 `user_id` 반환
          3. 이 `user_id`를 모든 API 요청의 헤더에 포함
          
          **예시:** `user-id: abc12345`
          """)
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
@app.get("/", 
         tags=["ℹ️ 서버 정보"], 
         summary="서버 상태 확인",
         description="서버가 정상적으로 작동하는지 확인합니다.")
async def root():
    return {"message": "실시간 욕설 감지 서버 (모듈화 버전)", "status": "running", "version": "2.0.0"}

# WebSocket 엔드포인트
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    ## 🎤 실시간 음성 욕설 감지 WebSocket
    
    **실시간으로 음성을 분석하여 욕설을 감지하는 WebSocket 연결입니다.**
    
    ### 📡 연결 방법
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/ws?user_id={your_user_id}');
    ```
    
    ### 🔐 인증
    - URL 파라미터로 `user_id` 전달 필수
    - 유효하지 않은 `user_id`인 경우 연결이 자동으로 끊어집니다
    
    ### 📤 클라이언트 → 서버 (음성 데이터 전송)
    **데이터 형식**: 바이너리 (Int16Array)
    ```javascript
    // 마이크에서 캡처한 오디오를 Int16 형식으로 변환하여 전송
    const int16Buffer = new Int16Array(audioData.length);
    for (let i = 0; i < audioData.length; i++) {
        int16Buffer[i] = audioData[i] * 32768;
    }
    ws.send(int16Buffer.buffer);
    ```
    
    ### 📥 서버 → 클라이언트 (감지 결과)
    **응답 형식**: JSON
    
    **욕설 감지된 경우:**
    ```json
    {
        "detected": true,
        "text": "인식된 음성 텍스트",
        "pattern": "감지된 욕설 패턴",
        "patterns": ["패턴1", "패턴2"],
        "confidence": 0.85,
        "energy": 0.042,
        "timestamp": "2024-01-01T12:00:00"
    }
    ```
    
    **정상 음성인 경우:**
    ```json
    {
        "detected": false,
        "text": "인식된 정상 음성",
        "energy": 0.035,
        "timestamp": "2024-01-01T12:00:00"
    }
    ```
    
    **음성 활동 없음:**
    ```json
    {
        "detected": false,
        "text": "",
        "energy": 0.001,
        "message": "음성 활동 없음"
    }
    ```
    
    ### ⚙️ 오디오 설정 권장사항
    ```javascript
    const constraints = {
        audio: {
            sampleRate: 16000,      // 16kHz 샘플링
            channelCount: 1,        // 모노 채널
            echoCancellation: true, // 에코 제거
            noiseSuppression: true  // 노이즈 억제
        }
    };
    ```
    
    ### 🔄 처리 과정
    1. **음성 수집**: 클라이언트에서 실시간 마이크 데이터 전송
    2. **버퍼링**: 1.5초 단위로 오디오 데이터 축적
    3. **음성인식**: Whisper 모델로 음성을 텍스트로 변환
    4. **욕설 감지**: 변환된 텍스트에서 욕설 패턴 검색
    5. **결과 전송**: 감지 결과를 실시간으로 클라이언트에 전송
    6. **DB 저장**: 욕설 감지 시 사용자별 기록 저장
    
    ### ⚡ 성능 최적화
    - **음성 활동 감지**: 에너지 레벨 0.02 이상일 때만 STT 실행
    - **50% 오버랩**: 연속성 보장을 위한 버퍼 겹침 처리
    - **메모리 관리**: 최대 3초 버퍼 크기 제한
    
    ### 🚨 연결 종료 사유
    - 잘못된 `user_id` 제공
    - 사용자 인증 실패
    - 네트워크 연결 문제
    - 클라이언트에서 연결 종료
    
    ### 💡 사용 팁
    - 안정적인 WiFi 환경에서 사용 권장
    - 마이크 권한 허용 필수
    - 배경 소음이 적은 환경에서 더 정확한 감지
    - 브라우저별 WebSocket 지원 확인
    """
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
            # 메시지 타입 확인
            message = await websocket.receive()
            
            # 연결 종료 메시지 처리
            if message["type"] == "websocket.disconnect":
                break
            
            # 텍스트 메시지 처리 (ping, 상태 확인 등)
            if message["type"] == "websocket.receive" and "text" in message:
                text_data = message["text"]
                if text_data == "ping":
                    await websocket.send_json({"type": "pong", "message": "연결 활성"})
                    continue
                elif text_data == "close":
                    break
                else:
                    await websocket.send_json({"type": "error", "message": "오디오 데이터가 필요합니다"})
                    continue
            
            # 바이너리 데이터 처리 (오디오)
            if message["type"] == "websocket.receive" and "bytes" in message:
                audio_data = message["bytes"]
                
                # 바이트를 NumPy 배열로 변환
                audio_chunk = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                # 예상하지 못한 메시지 타입
                await websocket.send_json({"type": "error", "message": "지원하지 않는 메시지 타입입니다"})
                continue
            
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
                            # DB 저장 (외래키 사용)
                            conn = db.get_connection()
                            user_model = User(conn)
                            internal_user_id = user_model.get_user_internal_id(user_id)
                            
                            if internal_user_id:
                                conn.execute(
                                    "INSERT INTO detections (user_id, text, pattern, patterns, confidence, audio_level, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (internal_user_id, recognized_text, result["pattern"], ",".join(result["patterns"]), 
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
        print(f"🔴 사용자 {user_id} 연결 끊김 (클라이언트가 연결 종료)")
    except Exception as e:
        print(f"❌ WebSocket 에러 - 사용자 {user_id}: {str(e)}")
        try:
            await websocket.send_json({"type": "error", "message": f"서버 에러: {str(e)}"})
        except:
            pass

# 감지 레벨 설정
@app.post("/sensitivity", 
          tags=["⚙️ 설정"], 
          summary="감지 레벨 설정",
          description="""
          **욕설 감지 민감도 조정**
          
          어떤 수준의 욕설까지 감지할지 설정합니다.
          
          **레벨 설명:**
          - **1단계 (강)**: 심한 욕설만 감지
          - **2단계 (중)**: 일반적인 욕설 감지 (기본값)
          - **3단계 (약)**: 가벼운 욕설까지 모두 감지
          
          **추천:**
          - 처음 사용하시면 2단계부터 시작
          - 점차 3단계로 올려가면서 욕설 줄이기
          """)
async def set_sensitivity(request: SensitivityRequest, user_id: Optional[str] = Header(None)):
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    if profanity_service.set_sensitivity(request.sensitivity):
        return {"sensitivity": request.sensitivity, "message": "감지 레벨 변경 완료"}
    else:
        raise HTTPException(status_code=400, detail="잘못된 감지 레벨입니다 (1, 2, 3만 가능)")

@app.get("/sensitivity", 
          tags=["⚙️ 설정"], 
          summary="현재 감지 레벨 조회",
          description="현재 설정된 욕설 감지 레벨을 확인합니다.")
async def get_sensitivity(user_id: Optional[str] = Header(None)):
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    return {"sensitivity": profanity_service.sensitivity_level}

# 통계 조회
@app.get("/stats", 
          tags=["📊 통계 화면"], 
          summary="기본 통계 조회",
          description="""
          **오늘/이번주 욕설 사용 요약**
          
          간단한 통계 정보를 제공합니다.
          
          **제공 정보:**
          - 오늘 욕설 감지 횟수와 평균 신뢰도
          - 이번 주 욕설 감지 횟수와 평균 신뢰도
          
          **더 자세한 통계는 `/stats/detailed` API를 사용하세요!**
          """)
async def get_stats(user_id: Optional[str] = Header(None)):
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    internal_user_id = user_model.get_user_internal_id(user_id)
    
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    # 오늘 통계 (외래키 사용)
    cursor = conn.execute(
        "SELECT COUNT(*), AVG(confidence) FROM detections WHERE user_id = ? AND date(timestamp) = date('now')",
        (internal_user_id,)
    )
    today_count, today_avg = cursor.fetchone()
    
    # 일주일 통계 (외래키 사용)
    cursor = conn.execute(
        "SELECT COUNT(*), AVG(confidence) FROM detections WHERE user_id = ? AND date(timestamp) >= date('now', '-7 days')",
        (internal_user_id,)
    )
    week_count, week_avg = cursor.fetchone()
    
    conn.close()
    
    return {
        "today": {"count": today_count or 0, "avg_confidence": round(today_avg or 0, 2)},
        "week": {"count": week_count or 0, "avg_confidence": round(week_avg or 0, 2)}
    }

# 사용자별 욕설 기록 조회
@app.get("/detections", 
          tags=["📊 통계 화면"], 
          summary="욕설 감지 기록",
          description="""
          **최근 욕설 감지 상세 기록**
          
          언제, 어떤 욕설을 사용했는지 상세한 기록을 확인합니다.
          
          **제공 정보:**
          - 감지된 텍스트와 욕설 패턴
          - 감지 신뢰도와 음성 레벨
          - 정확한 감지 시간
          - 사용자명 정보
          
          **활용 방안:**
          - 개인 욕설 사용 패턴 분석
          - 특정 시간대 욕설 사용 확인
          - 개선 효과 검증
          """)
async def get_detections(user_id: Optional[str] = Header(None), limit: int = 10):
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    internal_user_id = user_model.get_user_internal_id(user_id)
    
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    # 최근 욕설 감지 기록 조회 (JOIN으로 사용자 정보도 함께)
    cursor = conn.execute("""
        SELECT d.text, d.pattern, d.patterns, d.confidence, d.audio_level, d.timestamp, u.username
        FROM detections d
        JOIN users u ON d.user_id = u.id
        WHERE d.user_id = ?
        ORDER BY d.timestamp DESC
        LIMIT ?
    """, (internal_user_id, limit))
    
    results = cursor.fetchall()
    conn.close()
    
    detections = []
    for row in results:
        detections.append({
            "text": row[0],
            "pattern": row[1],
            "patterns": row[2].split(",") if row[2] else [],
            "confidence": row[3],
            "audio_level": row[4],
            "timestamp": row[5],
            "username": row[6]
        })
    
    return {"detections": detections, "count": len(detections)}

# ========== 메인 스크린 API ==========

@app.post("/recording/on", 
          tags=["📱 메인 화면"], 
          summary="녹음 시작",
          description="""
          ## 🎤 녹음 시작 API
          
          **메인 화면에서 녹음을 시작할 때 호출하는 API입니다.**
          
          ### 🔄 프론트엔드 사용 플로우 (전체)
          
          #### 1단계: 사용자 인증
          ```
          POST /auth/login
          → user_id 획득
          ```
          
          #### 2단계: 녹음 시작 준비
          ```
          POST /recording/on
          Headers: user-id: {user_id}
          → 서버에 녹음 시작 알림
          ```
          
          #### 3단계: WebSocket 연결
          ```javascript
          const ws = new WebSocket('ws://localhost:8000/ws?user_id={user_id}');
          ws.onopen = () => console.log('WebSocket 연결됨');
          ```
          
          #### 4단계: 마이크 권한 요청 및 오디오 캡처
          ```javascript
          const stream = await navigator.mediaDevices.getUserMedia({
              audio: {
                  sampleRate: 16000,
                  channelCount: 1,
                  echoCancellation: true,
                  noiseSuppression: true
              }
          });
          ```
          
          #### 5단계: 실시간 오디오 데이터 전송
          ```javascript
          processor.onaudioprocess = function(e) {
              const inputBuffer = e.inputBuffer.getChannelData(0);
              const int16Buffer = new Int16Array(inputBuffer.length);
              for (let i = 0; i < inputBuffer.length; i++) {
                  int16Buffer[i] = inputBuffer[i] * 32768;
              }
              ws.send(int16Buffer.buffer); // 실시간 전송
          };
          ```
          
          #### 6단계: 실시간 결과 수신 및 처리
          ```javascript
          ws.onmessage = function(event) {
              const data = JSON.parse(event.data);
              if (data.detected) {
                  // 욕설 감지됨 - UI 업데이트
                  showSwearAlert(data.text, data.patterns);
              } else {
                  // 정상 음성 - 로그만 기록
                  updateActivityLog(data.text);
              }
          };
          ```
          
          ### ⚠️ 주의사항
          - 이 API 호출 후 반드시 WebSocket 연결 필요
          - 마이크 권한이 허용되어야 함
          - 안정적인 네트워크 환경 필요
          """)
async def start_recording(user_id: Optional[str] = Header(None)):
    """녹음 시작"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    return {
        "message": "녹음 시작됨", 
        "recording": True,
        "next_step": "WebSocket 연결 필요",
        "websocket_url": f"ws://localhost:8000/ws?user_id={user_id}"
    }

@app.post("/recording/off",
          tags=["📱 메인 화면"], 
          summary="녹음 중지",
          description="""
          ## 🛑 녹음 중지 API
          
          **메인 화면에서 녹음을 중지할 때 호출하는 API입니다.**
          
          ### 🔄 녹음 중지 플로우
          
          #### 1단계: 녹음 중지 API 호출
          ```
          POST /recording/off
          Headers: user-id: {user_id}
          → 서버에 녹음 중지 알림
          ```
          
          #### 2단계: WebSocket 연결 종료
          ```javascript
          ws.close(); // WebSocket 연결 종료
          ```
          
          #### 3단계: 오디오 리소스 정리
          ```javascript
          if (audioContext) {
              audioContext.close();
              audioContext = null;
          }
          if (stream) {
              stream.getTracks().forEach(track => track.stop());
          }
          ```
          
          #### 4단계: UI 상태 업데이트
          ```javascript
          // 녹음 버튼을 "시작" 상태로 변경
          recordButton.textContent = "녹음 시작";
          recordButton.disabled = false;
          ```
          
          ### 📊 중지 후 할 수 있는 작업
          
          #### 개인 통계 확인
          ```
          GET /stats/detailed?period=today
          GET /stats/words
          ```
          
          #### 그룹 순위 확인
          ```
          GET /groups/my
          GET /groups/{group_id}/ranking?period=today
          ```
          
          ### 💡 권장사항
          - 녹음 중지 후 리소스 정리 필수
          - 배터리 절약을 위해 사용하지 않을 때는 반드시 중지
          - 중지 후 통계 확인으로 사용자에게 피드백 제공
          """)
async def stop_recording(user_id: Optional[str] = Header(None)):
    """녹음 중지"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    return {
        "message": "녹음 중지됨", 
        "recording": False,
        "next_steps": [
            "WebSocket 연결 종료",
            "오디오 리소스 정리",
            "통계 확인 가능"
        ]
    }

# ========== 통계 스크린 API ==========

@app.get("/stats/detailed", 
          tags=["📊 통계 화면"], 
          summary="상세 통계 조회",
          description="""
          **시간대별/기간별 욕설 사용 통계**
          
          사용자의 욕설 사용 패턴을 시간대별로 분석합니다.
          
          **기간 옵션:**
          - `today`: 오늘 시간대별 (10시, 12시, 14시, 16시, 18시, 20시, 22시)
          - `week`: 이번 주 일별 통계
          - `month`: 이번 달 4일 단위 통계
          
          **활용 방안:**
          - 언제 욕설을 많이 사용하는지 패턴 분석
          - 시간대별 개선 목표 설정
          - 그래프 차트로 시각화 가능
          """)
async def get_detailed_stats(
    period: str = "today",  # today, week, month
    user_id: Optional[str] = Header(None)
):
    """상세 통계 조회 (시간대별)"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    internal_user_id = user_model.get_user_internal_id(user_id)
    
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    if period == "today":
        # 오늘 시간대별 통계 (10시, 12시, 14시, 16시, 18시, 20시, 22시)
        time_slots = ["10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"]
        stats = []
        
        for i, time_slot in enumerate(time_slots):
            next_time = time_slots[i+1] if i+1 < len(time_slots) else "23:59"
            cursor = conn.execute("""
                SELECT COUNT(*) FROM detections 
                WHERE user_id = ? 
                AND date(timestamp) = date('now')
                AND time(timestamp) >= ? AND time(timestamp) < ?
            """, (internal_user_id, time_slot, next_time))
            
            count = cursor.fetchone()[0]
            stats.append({"time": time_slot, "count": count})
    
    elif period == "week":
        # 이번 주 일별 통계
        stats = []
        for i in range(7):
            cursor = conn.execute("""
                SELECT COUNT(*) FROM detections 
                WHERE user_id = ? 
                AND date(timestamp) = date('now', ? || ' days')
            """, (internal_user_id, -i))
            
            count = cursor.fetchone()[0]
            cursor = conn.execute("SELECT date('now', ? || ' days')", (-i,))
            date = cursor.fetchone()[0]
            stats.append({"date": date, "count": count})
    
    elif period == "month":
        # 이번 달 4일 단위 통계
        stats = []
        for i in range(0, 28, 4):
            start_date = f"-{i} days"
            end_date = f"-{i+4} days"
            
            cursor = conn.execute("""
                SELECT COUNT(*) FROM detections 
                WHERE user_id = ? 
                AND date(timestamp) >= date('now', ?)
                AND date(timestamp) < date('now', ?)
            """, (internal_user_id, end_date, start_date))
            
            count = cursor.fetchone()[0]
            cursor = conn.execute("SELECT date('now', ?)", (start_date,))
            date = cursor.fetchone()[0]
            stats.append({"period": f"{date} ~ 4일간", "count": count})
    
    conn.close()
    return {"period": period, "stats": stats}

@app.get("/stats/words", 
          tags=["📊 통계 화면"], 
          summary="욕설 단어별 통계",
          description="""
          **내가 사용한 욕설 단어와 횟수**
          
          어떤 욕설을 얼마나 사용했는지 실시간으로 확인할 수 있습니다.
          
          **특징:**
          - 사용 빈도 높은 순으로 정렬
          - 최대 20개 단어까지 표시
          - 실시간 업데이트
          
          **활용 방안:**
          - 자주 사용하는 욕설 파악
          - 특정 단어 줄이기 목표 설정
          - 개선 진행상황 추적
          """)
async def get_word_stats(user_id: Optional[str] = Header(None)):
    """욕한 단어와 횟수 통계"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    internal_user_id = user_model.get_user_internal_id(user_id)
    
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    cursor = conn.execute("""
        SELECT pattern, COUNT(*) as count
        FROM detections 
        WHERE user_id = ? AND pattern IS NOT NULL
        GROUP BY pattern
        ORDER BY count DESC
        LIMIT 20
    """, (internal_user_id,))
    
    word_stats = []
    for row in cursor.fetchall():
        word_stats.append({
            "word": row[0],
            "count": row[1]
        })
    
    conn.close()
    return {"words": word_stats}

# ========== 그룹 스크린 API ==========

@app.post("/groups/create", 
          tags=["👥 그룹 화면"], 
          summary="그룹 생성",
          description="""
          **새로운 그룹 만들기**
          
          친구들과 함께 욕설 줄이기 챌린지를 할 수 있는 그룹을 만듭니다.
          
          **기능:**
          - 6자리 고유 초대 코드 자동 생성
          - 최대 5명까지 참여 가능
          - 내기 마감일 설정 가능 (날짜 + 시간 별도 입력)
          - 그룹 생성자가 자동으로 관리자가 됨
          
          **마감일 설정:**
          - bet_deadline_date: YYYY-MM-DD 형식 (예: "2024-12-31")
          - bet_deadline_time: HH:MM 형식 (예: "23:59")
          - 시간 생략 시 자동으로 23:59:59로 설정
          
          **초대 코드로 친구들을 초대하세요!**
          """)
async def create_group(group_data: GroupCreate, user_id: Optional[str] = Header(None)):
    """그룹 생성"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    group_model = Group(conn)
    
    internal_user_id = user_model.get_user_internal_id(user_id)
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    # 날짜와 시간을 합쳐서 bet_deadline 생성
    bet_deadline = None
    if group_data.bet_deadline_date:
        if group_data.bet_deadline_time:
            # 날짜와 시간 모두 있으면 합치기
            bet_deadline = f"{group_data.bet_deadline_date} {group_data.bet_deadline_time}:00"
        else:
            # 날짜만 있으면 23:59:59로 설정
            bet_deadline = f"{group_data.bet_deadline_date} 23:59:59"
    
    result = group_model.create_group(group_data.name, internal_user_id, bet_deadline)
    conn.close()
    
    if result["success"]:
        return {
            "group_id": result["group_id"],
            "invite_code": result["invite_code"],
            "message": result["message"]
        }
    else:
        raise HTTPException(status_code=400, detail=result["message"])

@app.post("/groups/join", 
          tags=["👥 그룹 화면"], 
          summary="그룹 참여",
          description="""
          **초대 코드로 그룹 참여하기**
          
          친구가 공유한 6자리 초대 코드를 입력하여 그룹에 참여합니다.
          
          **참여 조건:**
          - 유효한 초대 코드 필요
          - 그룹 정원이 남아있어야 함 (기본 최대 5명)
          - 이미 참여한 그룹에는 중복 참여 불가
          
          **참여 후 그룹 순위와 챌린지에 함께 참여할 수 있습니다!**
          """)
async def join_group(group_data: GroupJoin, user_id: Optional[str] = Header(None)):
    """그룹 참여"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    group_model = Group(conn)
    
    internal_user_id = user_model.get_user_internal_id(user_id)
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    result = group_model.join_group(group_data.invite_code, internal_user_id)
    conn.close()
    
    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=400, detail=result["message"])

@app.get("/groups/my", 
          tags=["👥 그룹 화면"], 
          summary="내 그룹 목록",
          description="""
          **참여 중인 그룹 목록 조회**
          
          현재 사용자가 참여하고 있는 모든 그룹의 정보를 확인합니다.
          
          **제공 정보:**
          - 그룹명과 초대 코드
          - 그룹 소유자 정보
          - 현재 멤버 수
          - 내가 관리자인지 여부
          - 그룹 생성일
          
          **그룹을 터치하면 상세 정보와 순위를 볼 수 있습니다!**
          """)
async def get_my_groups(user_id: Optional[str] = Header(None)):
    """내가 참여한 그룹 목록"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    group_model = Group(conn)
    
    internal_user_id = user_model.get_user_internal_id(user_id)
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    groups = group_model.get_user_groups(internal_user_id)
    conn.close()
    
    return {"groups": groups}

@app.post("/groups/banned-words", 
          tags=["👥 그룹 화면"], 
          summary="그룹 금지어 추가",
          description="""
          **그룹 전용 금지어 설정**
          
          그룹만의 특별한 금지어를 추가할 수 있습니다.
          
          **권한:**
          - 그룹 관리자(생성자)만 추가 가능
          - 기본 욕설 패턴 외에 추가로 감지됨
          
          **활용 예시:**
          - "바보", "멍청이" 같은 가벼운 욕설 추가
          - 그룹 내 농담이지만 줄이고 싶은 말들
          - 특정 상황에서만 사용하는 말들
          
          **추가된 금지어는 실시간으로 감지됩니다!**
          """)
async def add_banned_word(word_data: BannedWordAdd, user_id: Optional[str] = Header(None)):
    """그룹 금지어 추가"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    group_model = Group(conn)
    
    internal_user_id = user_model.get_user_internal_id(user_id)
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    result = group_model.add_banned_word(word_data.group_id, word_data.word, internal_user_id)
    conn.close()
    
    if result["success"]:
        return {"message": result["message"]}
    else:
        raise HTTPException(status_code=400, detail=result["message"])

@app.get("/groups/{group_id}/banned-words", 
          tags=["👥 그룹 화면"], 
          summary="그룹 금지어 목록",
          description="""
          **현재 그룹의 금지어 목록 조회**
          
          해당 그룹에서 설정된 모든 금지어를 확인할 수 있습니다.
          
          **포함 내용:**
          - 그룹 관리자가 추가한 사용자 정의 금지어
          - 추가된 순서대로 정렬
          
          **참고:**
          - 기본 욕설 패턴은 별도로 관리됨
          - 그룹 멤버 누구나 목록 조회 가능
          """)
async def get_banned_words(group_id: int, user_id: Optional[str] = Header(None)):
    """그룹 금지어 목록"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    group_model = Group(conn)
    
    banned_words = group_model.get_banned_words(group_id)
    conn.close()
    
    return {"banned_words": banned_words}

@app.get("/groups/{group_id}/ranking", 
          tags=["👥 그룹 화면"], 
          summary="그룹 내 순위",
          description="""
          **그룹 멤버들의 욕설 사용 순위**
          
          그룹 내에서 누가 욕설을 가장 많이/적게 사용했는지 확인합니다.
          
          **기간 설정:**
          - `today`: 오늘 하루
          - `week`: 이번 주 (기본값)
          - `month`: 이번 달
          - `all`: 전체 기간
          
          **순위 정보:**
          - **Best Performers**: 욕설을 적게 사용한 상위 3명 🏆
          - **Worst Performers**: 욕설을 많이 사용한 하위 3명 😅
          - **Full Ranking**: 전체 멤버 순위
          
          **친구들과 함께 욕설 줄이기 챌린지하세요!**
          """)
async def get_group_ranking(
    group_id: int, 
    period: str = "week",  # today, week, month, all
    user_id: Optional[str] = Header(None)
):
    """그룹 내 욕설 순위"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    group_model = Group(conn)
    
    ranking = group_model.get_group_ranking(group_id, period)
    conn.close()
    
    # 최고 순위와 최저 순위 분리
    best_users = ranking[:3]  # 상위 3명
    worst_users = ranking[-3:] if len(ranking) > 3 else []  # 하위 3명
    
    return {
        "period": period,
        "total_members": len(ranking),
        "best_performers": best_users,  # 욕설 적게 한 순
        "worst_performers": worst_users,  # 욕설 많이 한 순
        "full_ranking": ranking
    }

if __name__ == "__main__":
    import uvicorn
    print("🚀 모듈화된 실시간 욕설 감지 서버 시작")
    uvicorn.run(app, host="0.0.0.0", port=8000)