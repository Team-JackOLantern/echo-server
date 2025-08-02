import sqlite3
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models.database import Database
from models.user import User
from models.group import Group
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

# Pydantic 모델들
class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str


class GroupCreate(BaseModel):
    name: str
    bet_deadline_date: Optional[str] = None  # YYYY-MM-DD 형식
    bet_deadline_time: Optional[str] = None  # HH:MM 형식

class GroupJoin(BaseModel):
    invite_code: str

class BannedWordAdd(BaseModel):
    group_id: int
    word: str

class ProfanityDetect(BaseModel):
    text: str

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

# 욕설 저장 API
@app.post("/save-profanity", 
          tags=["💾 욕설 저장"], 
          summary="욕설 저장",
          description="""
          **프론트엔드에서 이미 감지된 욕설을 DB에 저장합니다.**
          
          ### 📤 요청 형식
          ```json
          {
              "text": "감지된 욕설 내용"
          }
          ```
          
          ### 📥 응답 형식 (실제 예시)
          ```json
          {
              "success": true,
              "text": "시발 진짜 짜증나네",
              "timestamp": "2025-08-02T14:30:15",
              "message": "욕설이 성공적으로 저장되었습니다"
          }
          ```
          
          ### 💡 프론트엔드 처리 가이드
          - `success: true` → 성공 토스트 메시지 표시
          - `text` → 저장된 욕설 내용 (로그용)
          - `timestamp` → 정확한 감지 시간 기록
          - 통계 카운터 즉시 업데이트 (+1)
          
          ### 🔐 인증
          - 헤더에 `user-id` 필수
          - 유효하지 않은 `user_id`인 경우 401 에러
          
          ### 💾 저장 정보
          - 프론트엔드에서 이미 감지된 욕설만 전송
          - 서버는 별도 감지 없이 바로 DB 저장
          - 통계 및 기록에 즉시 반영
          """)
async def save_profanity(request: ProfanityDetect, user_id: Optional[str] = Header(None)):
    """프론트엔드에서 감지된 욕설 저장"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    # DB 저장 (프론트엔드에서 이미 욕설임을 확인했으므로 바로 저장)
    conn = db.get_connection()
    user_model = User(conn)
    internal_user_id = user_model.get_user_internal_id(user_id)
    
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    # 욕설로 가정하고 저장 (pattern은 텍스트 자체로, confidence는 1.0으로)
    conn.execute(
        "INSERT INTO detections (user_id, text, pattern, patterns, confidence, audio_level, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (internal_user_id, request.text, request.text, request.text, 1.0, 0.0, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    
    print(f"💾 욕설 저장 완료! 사용자: {user_id}, 텍스트: '{request.text}'")
    
    return {
        "success": True,
        "text": request.text,
        "timestamp": datetime.now().isoformat(),
        "message": "욕설이 성공적으로 저장되었습니다"
    }



# 통계 조회
@app.get("/stats", 
          tags=["📊 통계 화면"], 
          summary="기본 통계 조회",
          description="""
          **오늘/이번주 욕설 사용 요약**
          
          간단한 통계 정보를 제공합니다.
          
          ### 📥 응답 예시
          ```json
          {
              "today": {"count": 12, "avg_confidence": 1.0},
              "week": {"count": 28, "avg_confidence": 1.0}
          }
          ```
          
          ### 📊 UI 활용
          - **오늘 vs 어제**: 개선/악화 추세 표시
          - **주간 평균**: `week.count / 7` = 일평균 계산
          - **목표 대비**: 설정한 목표와 비교하여 진행률 표시
          
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
          
          ### 📥 응답 예시 (limit=3)
          ```json
          {
              "detections": [
                  {
                      "text": "시발 진짜 짜증나네",
                      "pattern": "시발",
                      "patterns": ["시발"],
                      "confidence": 1.0,
                      "audio_level": 0.0,
                      "timestamp": "2025-08-02T22:30:00",
                      "username": "이재환"
                  },
                  {
                      "text": "병신같은 버그",
                      "pattern": "병신",
                      "patterns": ["병신"],
                      "confidence": 1.0,
                      "audio_level": 0.0,
                      "timestamp": "2025-08-02T21:15:00",
                      "username": "이재환"
                  },
                  {
                      "text": "씨발 답답해",
                      "pattern": "씨발",
                      "patterns": ["씨발"],
                      "confidence": 1.0,
                      "audio_level": 0.0,
                      "timestamp": "2025-08-02T20:25:00",
                      "username": "이재환"
                  }
              ],
              "count": 3
          }
          ```
          
          ### 🎯 프론트엔드 활용
          - **타임라인**: `timestamp` 순으로 시간대별 표시
          - **패턴 분석**: `pattern` 별로 그룹화하여 통계
          - **개선 추적**: 시간순으로 정렬하여 개선 추세 확인
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
          - `today`: 오늘 0시~24시 1시간 단위 (24개 데이터)
          - `week`: 이번 주 일별 통계
          - `month`: 이번 달 4일 단위 통계
          
          ### 📥 today 응답 예시 (실제 목 데이터)
          ```json
          {
              "period": "today",
              "stats": [
                  {"hour": "00:00", "count": 0},
                  {"hour": "01:00", "count": 0},
                  {"hour": "02:00", "count": 0},
                  {"hour": "03:00", "count": 0},
                  {"hour": "04:00", "count": 0},
                  {"hour": "05:00", "count": 0},
                  {"hour": "06:00", "count": 0},
                  {"hour": "07:00", "count": 2},
                  {"hour": "08:00", "count": 2},
                  {"hour": "09:00", "count": 1},
                  {"hour": "10:00", "count": 1},
                  {"hour": "11:00", "count": 1},
                  {"hour": "12:00", "count": 3},
                  {"hour": "13:00", "count": 2},
                  {"hour": "14:00", "count": 1},
                  {"hour": "15:00", "count": 2},
                  {"hour": "16:00", "count": 2},
                  {"hour": "17:00", "count": 2},
                  {"hour": "18:00", "count": 2},
                  {"hour": "19:00", "count": 2},
                  {"hour": "20:00", "count": 2},
                  {"hour": "21:00", "count": 1},
                  {"hour": "22:00", "count": 1},
                  {"hour": "23:00", "count": 3}
              ]
          }
          ```
          
          ### 📊 UI 활용 방안
          - **시간대별 히트맵**: 색상으로 욕설 빈도 표시
          - **라인 차트**: 하루 동안의 욕설 사용 패턴
          - **위험 시간대**: count가 높은 시간대 하이라이트
          - **개선 목표**: 특정 시간대 욕설 줄이기 목표 설정
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
        # 오늘 0시~24시 1시간 단위 통계
        stats = []
        
        for hour in range(24):
            hour_start = f"{hour:02d}:00:00"
            hour_end = f"{hour+1:02d}:00:00" if hour < 23 else "23:59:59"
            
            cursor = conn.execute("""
                SELECT COUNT(*) FROM detections 
                WHERE user_id = ? 
                AND date(timestamp) = date('now')
                AND time(timestamp) >= ? AND time(timestamp) < ?
            """, (internal_user_id, hour_start, hour_end))
            
            count = cursor.fetchone()[0]
            stats.append({"hour": f"{hour:02d}:00", "count": count})
    
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

@app.get("/stats/top-words", 
          tags=["📊 통계 화면"], 
          summary="상위 5개 욕설 조회",
          description="""
          **내가 가장 많이 사용한 상위 5개 욕설**
          
          가장 자주 사용하는 욕설 5개를 빈도순으로 반환합니다.
          
          ### 📥 응답 형식 (실제 목 데이터 예시)
          ```json
          {
              "top_words": [
                  {"word": "시발", "count": 8},
                  {"word": "씨발", "count": 7},
                  {"word": "병신", "count": 6},
                  {"word": "미친", "count": 5},
                  {"word": "개짜증", "count": 2}
              ],
              "total_count": 28
          }
          ```
          
          ### 📊 UI 활용 가이드
          - **워드 클라우드**: `word`와 `count`로 크기 조절
          - **진행률 바**: `count / total_count * 100`으로 비율 계산
          - **목표 설정**: 가장 많이 사용한 단어부터 줄이기 목표 설정
          
          ### 🔐 인증
          - 헤더에 `user-id` 필수
          
          ### 📊 활용 방안
          - 자주 사용하는 욕설 파악
          - 개선 목표 설정에 활용
          - 진행상황 추적
          """)
async def get_top_words(user_id: Optional[str] = Header(None)):
    """상위 5개 욕설 조회"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    internal_user_id = user_model.get_user_internal_id(user_id)
    
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    # 상위 5개 욕설 조회
    cursor = conn.execute("""
        SELECT pattern, COUNT(*) as count
        FROM detections 
        WHERE user_id = ? AND pattern IS NOT NULL
        GROUP BY pattern
        ORDER BY count DESC
        LIMIT 5
    """, (internal_user_id,))
    
    top_words = []
    total_count = 0
    for row in cursor.fetchall():
        word_data = {
            "word": row[0],
            "count": row[1]
        }
        top_words.append(word_data)
        total_count += row[1]
    
    conn.close()
    return {
        "top_words": top_words,
        "total_count": total_count
    }

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
          summary="내 그룹 목록 (상세정보)",
          description="""
          **참여 중인 그룹 목록과 상세 정보 한번에 조회**
          
          현재 사용자가 참여하고 있는 모든 그룹의 정보를 상세하게 확인합니다.
          
          ### 📥 응답 형식 (실제 목 데이터 예시)
          ```json
          {
              "groups": [
                  {
                      "id": 5,
                      "name": "욕설 줄이기 챌린지",
                      "invite_code": "KDY7ZX",
                      "owner_id": 2,
                      "bet_deadline": "2025-10-02 08:58:00",
                      "created_at": "2025-08-02 19:58:17",
                      "owner_name": "test",
                      "member_count": 5,
                      "is_owner": true,
                      "banned_words": ["바보", "멍청이", "아햏햏", "얼간이", "돌대가리"],
                      "most_profanity_users": [
                          {"username": "이재환", "count": 28},
                          {"username": "test", "count": 17},
                          {"username": "testuser", "count": 8},
                          {"username": "testuser2", "count": 3},
                          {"username": "재환", "count": 1}
                      ],
                      "least_profanity_users": [
                          {"username": "재환", "count": 1},
                          {"username": "testuser2", "count": 3},
                          {"username": "testuser", "count": 8},
                          {"username": "test", "count": 17},
                          {"username": "이재환", "count": 28}
                      ]
                  }
              ]
          }
          ```
          
          ### 📊 데이터 해석 가이드
          **욕설 순위 이해하기:**
          - `most_profanity_users`: 욕설을 많이 사용한 순서 (개선이 필요한 사용자들)
          - `least_profanity_users`: 욕설을 적게 사용한 순서 (모범적인 사용자들)
          - 같은 사용자가 두 배열에 모두 포함되며, 순서만 반대입니다
          
          **UI 활용 팁:**
          - 🔴 `most_profanity_users[0]`: "욕설 사용 1위" (빨간색 표시)
          - 🟡 `most_profanity_users[1-2]`: "주의 필요" (노란색 표시)  
          - 🟢 `least_profanity_users[0]`: "모범 사용자" (초록색 표시)
          - `banned_words`: 그룹 설정에서 관리, 관리자만 추가/삭제 가능
          
          ### 🔍 제공 정보
          - **기본 그룹 정보**: 그룹명, 초대코드, 소유자, 멤버수
          - **욕설 순위**: 가장 많이 사용한 사용자 순서
          - **모범 순위**: 가장 적게 사용한 사용자 순서  
          - **금지어 목록**: 그룹에서 설정한 금지어들
          - **관리자 여부**: 내가 관리자인지 확인
          
          ### 📊 활용 방안
          - 그룹별 상세 현황 파악
          - 멤버들의 욕설 사용 현황 비교
          - 그룹 관리 및 순위 확인
          """)
async def get_my_groups(user_id: Optional[str] = Header(None)):
    """내가 참여한 그룹 목록과 상세 정보"""
    if not user_id or not verify_user(user_id):
        raise HTTPException(status_code=401, detail="인증되지 않은 사용자입니다")
    
    conn = db.get_connection()
    user_model = User(conn)
    group_model = Group(conn)
    
    internal_user_id = user_model.get_user_internal_id(user_id)
    if not internal_user_id:
        conn.close()
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    
    # 기본 그룹 정보 조회
    groups = group_model.get_user_groups(internal_user_id)
    
    # 각 그룹별 상세 정보 추가
    for group in groups:
        group_id = group["id"]  # "group_id"가 아니라 "id"
        
        # 1. 금지어 목록 조회
        banned_words = group_model.get_banned_words(group_id)
        group["banned_words"] = banned_words  # 이미 문자열 리스트로 반환됨
        
        # 2. 그룹 멤버들의 욕설 사용 순위 조회 (가장 많이 사용한 순)
        cursor = conn.execute("""
            SELECT u.username, COUNT(d.id) as count
            FROM group_members gm
            JOIN users u ON gm.user_id = u.id
            LEFT JOIN detections d ON u.id = d.user_id
            WHERE gm.group_id = ?
            GROUP BY u.id, u.username
            ORDER BY count DESC
        """, (group_id,))
        
        all_members = []
        for row in cursor.fetchall():
            all_members.append({
                "username": row[0],
                "count": row[1]
            })
        
        # 3. 가장 많이 사용한 사용자들 (상위 멤버들)
        group["most_profanity_users"] = all_members
        
        # 4. 가장 적게 사용한 사용자들 (하위 멤버들 - 역순)
        group["least_profanity_users"] = list(reversed(all_members))
    
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

# ========== 개발/테스트용 API ==========

@app.delete("/admin/reset-all-data", 
           tags=["🔧 관리자"], 
           summary="모든 데이터 초기화",
           description="""
           **⚠️ 위험: 모든 테이블의 데이터를 완전히 삭제합니다**
           
           시연 영상 촬영이나 테스트 목적으로 모든 데이터를 초기화합니다.
           
           ### 🗑️ 삭제되는 데이터
           - **사용자 계정** (`users` 테이블)
           - **욕설 감지 기록** (`detections` 테이블)
           - **그룹 정보** (`groups` 테이블)
           - **그룹 멤버** (`group_members` 테이블)
           - **그룹 금지어** (`group_banned_words` 테이블)
           
           ### 📥 응답 예시
           ```json
           {
               "success": true,
               "message": "모든 데이터가 성공적으로 초기화되었습니다",
               "deleted_tables": [
                   "detections", "group_banned_words", "group_members", 
                   "groups", "users"
               ],
               "timestamp": "2025-08-02T23:30:00"
           }
           ```
           
           ### ⚠️ 주의사항
           - **돌이킬 수 없는 작업**입니다
           - **시연 목적**으로만 사용하세요
           - **외래키 제약조건** 순서에 따라 안전하게 삭제됩니다
           - 삭제 후 새로운 사용자 등록부터 다시 시작해야 합니다
           
           ### 🎬 시연 시나리오
           1. 이 API 호출로 데이터 초기화
           2. 새 사용자 등록 (`/auth/register`)
           3. 그룹 생성 (`/groups/create`)
           4. 욕설 저장 (`/save-profanity`)
           5. 통계 확인 (`/stats/*`)
           """)
async def reset_all_data():
    """모든 테이블 데이터 초기화 (시연용)"""
    try:
        conn = db.get_connection()
        
        # 외래키 제약조건 순서에 따라 삭제
        tables_to_clear = [
            "detections",           # 욕설 감지 기록
            "group_banned_words",   # 그룹 금지어
            "group_members",        # 그룹 멤버
            "groups",              # 그룹
            "users"                # 사용자
        ]
        
        deleted_tables = []
        
        for table in tables_to_clear:
            cursor = conn.execute(f"DELETE FROM {table}")
            deleted_count = cursor.rowcount
            deleted_tables.append(f"{table} ({deleted_count}개)")
            print(f"🗑️ {table} 테이블에서 {deleted_count}개 데이터 삭제")
        
        # AUTO_INCREMENT 카운터도 초기화
        for table in tables_to_clear:
            conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
        
        conn.commit()
        conn.close()
        
        print("✅ 모든 데이터 초기화 완료!")
        
        return {
            "success": True,
            "message": "모든 데이터가 성공적으로 초기화되었습니다",
            "deleted_tables": deleted_tables,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        print(f"❌ 데이터 초기화 실패: {str(e)}")
        raise HTTPException(status_code=500, detail=f"데이터 초기화 실패: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    print("🚀 모듈화된 실시간 욕설 감지 서버 시작")
    uvicorn.run(app, host="0.0.0.0", port=8000)