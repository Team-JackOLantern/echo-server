import sqlite3
from datetime import datetime
import os

class Database:
    def __init__(self, db_path="data.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """데이터베이스 초기화"""
        conn = sqlite3.connect(self.db_path)
        
        # 외래키 제약조건 활성화
        conn.execute("PRAGMA foreign_keys = ON")
        
        # 테이블 존재 여부 확인
        cursor = conn.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name IN ('users', 'detections', 'groups', 'group_members', 'group_banned_words')
        """)
        existing_tables = [row[0] for row in cursor.fetchall()]
        
        # 사용자 테이블이 없으면 생성
        if 'users' not in existing_tables:
            conn.execute("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    user_id TEXT UNIQUE NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("📝 users 테이블 생성됨")
        else:
            print("✅ users 테이블 이미 존재")
        
        # 그룹 테이블 생성
        if 'groups' not in existing_tables:
            conn.execute("""
                CREATE TABLE groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    invite_code TEXT UNIQUE NOT NULL,
                    owner_id INTEGER NOT NULL,
                    max_members INTEGER DEFAULT 5,
                    bet_deadline TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
                )
            """)
            print("📝 groups 테이블 생성됨")
        
        # 그룹 멤버 테이블 생성
        if 'group_members' not in existing_tables:
            conn.execute("""
                CREATE TABLE group_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                    UNIQUE(group_id, user_id)
                )
            """)
            print("📝 group_members 테이블 생성됨")
        
        # 그룹 금지어 테이블 생성
        if 'group_banned_words' not in existing_tables:
            conn.execute("""
                CREATE TABLE group_banned_words (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    word TEXT NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
                    FOREIGN KEY (added_by) REFERENCES users (id) ON DELETE CASCADE,
                    UNIQUE(group_id, word)
                )
            """)
            print("📝 group_banned_words 테이블 생성됨")
        
        # detections 테이블 스키마 확인 및 재생성 (외래키 적용을 위해)
        if 'detections' in existing_tables:
            # 기존 detections 테이블의 스키마 확인
            cursor = conn.execute("PRAGMA table_info(detections)")
            columns = [row[1] for row in cursor.fetchall()]
            
            # user_id가 TEXT 타입이면 INTEGER로 변경 필요
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE name='detections'")
            result = cursor.fetchone()
            schema = result[0] if result else ""
            
            if "user_id TEXT" in schema or "FOREIGN KEY" not in schema:
                print("🔄 detections 테이블 스키마 업데이트 필요")
                
                # 기존 데이터 백업
                conn.execute("""
                    CREATE TABLE detections_backup AS 
                    SELECT * FROM detections
                """)
                
                # 기존 테이블 삭제
                conn.execute("DROP TABLE detections")
                
                # 새 테이블 생성 (외래키 포함)
                conn.execute("""
                    CREATE TABLE detections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        text TEXT,
                        pattern TEXT,
                        patterns TEXT,
                        confidence REAL,
                        audio_level REAL,
                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                    )
                """)
                
                # 백업 테이블 삭제 (데이터 호환성 문제로 복구하지 않음)
                conn.execute("DROP TABLE detections_backup")
                print("🔄 detections 테이블 재생성됨 (외래키 적용)")
            else:
                print("✅ detections 테이블 이미 올바른 스키마로 존재")
        else:
            # detections 테이블 생성
            conn.execute("""
                CREATE TABLE detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    text TEXT,
                    pattern TEXT,
                    patterns TEXT,
                    confidence REAL,
                    audio_level REAL,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                )
            """)
            print("📝 detections 테이블 생성됨")
        
        # 인덱스 생성 (이미 존재하면 무시됨)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_user_id ON users (user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_detections_user_id ON detections (user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_detections_timestamp ON detections (timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_groups_invite_code ON groups (invite_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group_members_group_id ON group_members (group_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group_members_user_id ON group_members (user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group_banned_words_group_id ON group_banned_words (group_id)")
            print("📊 인덱스 생성/확인 완료")
        except Exception as e:
            print(f"⚠️ 인덱스 생성 중 오류 (무시됨): {e}")
        
        conn.commit()
        conn.close()
        print("🗄️ 데이터베이스 초기화 완료 (기존 users 데이터 보존)")
    
    def get_connection(self):
        """데이터베이스 연결 반환"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")  # 외래키 제약조건 활성화
        return conn