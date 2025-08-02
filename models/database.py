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
        
        # 사용자 테이블
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                user_id TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 욕설 감지 기록 테이블
        conn.execute("""
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                text TEXT,
                pattern TEXT,
                patterns TEXT,
                confidence REAL,
                audio_level REAL,
                timestamp TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        
        conn.commit()
        conn.close()
        print("🗄️ 데이터베이스 초기화 완료")
    
    def get_connection(self):
        """데이터베이스 연결 반환"""
        return sqlite3.connect(self.db_path)