import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Dict

class User:
    def __init__(self, db_connection):
        self.conn = db_connection
    
    def create_user(self, username: str, password: str) -> Dict:
        """새 사용자 생성"""
        try:
            user_id = str(uuid.uuid4())[:8]  # 8자리 랜덤 ID
            
            cursor = self.conn.execute(
                "INSERT INTO users (username, password, user_id) VALUES (?, ?, ?)",
                (username, password, user_id)
            )
            self.conn.commit()
            
            return {
                "success": True,
                "user_id": user_id,
                "message": "사용자 생성 완료"
            }
        except sqlite3.IntegrityError:
            return {
                "success": False,
                "message": "이미 존재하는 사용자명입니다"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"사용자 생성 실패: {str(e)}"
            }
    
    def authenticate(self, username: str, password: str) -> Dict:
        """사용자 인증"""
        cursor = self.conn.execute(
            "SELECT user_id FROM users WHERE username = ? AND password = ?",
            (username, password)
        )
        result = cursor.fetchone()
        
        if result:
            return {
                "success": True,
                "user_id": result[0],
                "message": "인증 성공"
            }
        else:
            return {
                "success": False,
                "message": "잘못된 사용자명 또는 비밀번호"
            }
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """사용자 ID로 사용자 정보 조회"""
        cursor = self.conn.execute(
            "SELECT id, username, user_id, created_at FROM users WHERE user_id = ?",
            (user_id,)
        )
        result = cursor.fetchone()
        
        if result:
            return {
                "id": result[0],
                "username": result[1],
                "user_id": result[2],
                "created_at": result[3]
            }
        return None