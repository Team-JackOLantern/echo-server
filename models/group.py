import sqlite3
import uuid
import random
import string
from datetime import datetime
from typing import Optional, Dict, List

class Group:
    def __init__(self, db_connection):
        self.conn = db_connection
    
    def generate_invite_code(self) -> str:
        """6자리 초대 코드 생성"""
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    def create_group(self, name: str, owner_id: int, bet_deadline: Optional[str] = None) -> Dict:
        """그룹 생성"""
        try:
            # 고유한 초대 코드 생성
            while True:
                invite_code = self.generate_invite_code()
                cursor = self.conn.execute("SELECT id FROM groups WHERE invite_code = ?", (invite_code,))
                if not cursor.fetchone():
                    break
            
            cursor = self.conn.execute("""
                INSERT INTO groups (name, invite_code, owner_id, bet_deadline) 
                VALUES (?, ?, ?, ?)
            """, (name, invite_code, owner_id, bet_deadline))
            
            group_id = cursor.lastrowid
            
            # 그룹 생성자를 멤버로 자동 추가
            self.conn.execute("""
                INSERT INTO group_members (group_id, user_id) VALUES (?, ?)
            """, (group_id, owner_id))
            
            self.conn.commit()
            
            return {
                "success": True,
                "group_id": group_id,
                "invite_code": invite_code,
                "message": "그룹 생성 완료"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"그룹 생성 실패: {str(e)}"
            }
    
    def join_group(self, invite_code: str, user_id: int) -> Dict:
        """그룹 참여"""
        try:
            # 그룹 조회
            cursor = self.conn.execute("""
                SELECT id, name, max_members FROM groups WHERE invite_code = ?
            """, (invite_code,))
            group = cursor.fetchone()
            
            if not group:
                return {"success": False, "message": "존재하지 않는 초대 코드입니다"}
            
            group_id, group_name, max_members = group
            
            # 이미 멤버인지 확인
            cursor = self.conn.execute("""
                SELECT id FROM group_members WHERE group_id = ? AND user_id = ?
            """, (group_id, user_id))
            if cursor.fetchone():
                return {"success": False, "message": "이미 그룹에 참여하고 있습니다"}
            
            # 멤버 수 확인
            cursor = self.conn.execute("""
                SELECT COUNT(*) FROM group_members WHERE group_id = ?
            """, (group_id,))
            current_members = cursor.fetchone()[0]
            
            if current_members >= max_members:
                return {"success": False, "message": f"그룹 최대 인원({max_members}명)을 초과했습니다"}
            
            # 그룹 참여
            self.conn.execute("""
                INSERT INTO group_members (group_id, user_id) VALUES (?, ?)
            """, (group_id, user_id))
            
            self.conn.commit()
            
            return {
                "success": True,
                "group_id": group_id,
                "group_name": group_name,
                "message": "그룹 참여 완료"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"그룹 참여 실패: {str(e)}"
            }
    
    def get_user_groups(self, user_id: int) -> List[Dict]:
        """사용자가 참여한 그룹 목록"""
        cursor = self.conn.execute("""
            SELECT g.id, g.name, g.invite_code, g.owner_id, g.bet_deadline, g.created_at,
                   u.username as owner_name,
                   (SELECT COUNT(*) FROM group_members WHERE group_id = g.id) as member_count
            FROM groups g
            JOIN group_members gm ON g.id = gm.group_id
            JOIN users u ON g.owner_id = u.id
            WHERE gm.user_id = ?
            ORDER BY g.created_at DESC
        """, (user_id,))
        
        groups = []
        for row in cursor.fetchall():
            groups.append({
                "id": row[0],
                "name": row[1],
                "invite_code": row[2],
                "owner_id": row[3],
                "bet_deadline": row[4],
                "created_at": row[5],
                "owner_name": row[6],
                "member_count": row[7],
                "is_owner": row[3] == user_id
            })
        
        return groups
    
    def add_banned_word(self, group_id: int, word: str, added_by: int) -> Dict:
        """그룹 금지어 추가"""
        try:
            # 그룹 권한 확인 (소유자만 가능)
            cursor = self.conn.execute("SELECT owner_id FROM groups WHERE id = ?", (group_id,))
            group = cursor.fetchone()
            
            if not group:
                return {"success": False, "message": "존재하지 않는 그룹입니다"}
            
            if group[0] != added_by:
                return {"success": False, "message": "그룹 소유자만 금지어를 추가할 수 있습니다"}
            
            # 금지어 추가
            self.conn.execute("""
                INSERT INTO group_banned_words (group_id, word, added_by) VALUES (?, ?, ?)
            """, (group_id, word.lower(), added_by))
            
            self.conn.commit()
            
            return {"success": True, "message": "금지어 추가 완료"}
        except sqlite3.IntegrityError:
            return {"success": False, "message": "이미 존재하는 금지어입니다"}
        except Exception as e:
            return {"success": False, "message": f"금지어 추가 실패: {str(e)}"}
    
    def get_banned_words(self, group_id: int) -> List[str]:
        """그룹 금지어 목록"""
        cursor = self.conn.execute("""
            SELECT word FROM group_banned_words WHERE group_id = ? ORDER BY created_at
        """, (group_id,))
        
        return [row[0] for row in cursor.fetchall()]
    
    def get_group_ranking(self, group_id: int, period: str = "week") -> List[Dict]:
        """그룹 내 욕설 순위"""
        # 기간 조건 설정
        if period == "today":
            date_condition = "date(d.timestamp) = date('now')"
        elif period == "week":
            date_condition = "date(d.timestamp) >= date('now', '-7 days')"
        elif period == "month":
            date_condition = "date(d.timestamp) >= date('now', '-30 days')"
        else:
            date_condition = "1=1"  # 전체 기간
        
        cursor = self.conn.execute(f"""
            SELECT u.id, u.username, COUNT(d.id) as swear_count
            FROM users u
            JOIN group_members gm ON u.id = gm.user_id
            LEFT JOIN detections d ON u.id = d.user_id AND {date_condition}
            WHERE gm.group_id = ?
            GROUP BY u.id, u.username
            ORDER BY swear_count DESC
        """, (group_id,))
        
        ranking = []
        for i, row in enumerate(cursor.fetchall(), 1):
            ranking.append({
                "rank": i,
                "user_id": row[0],
                "username": row[1],
                "swear_count": row[2]
            })
        
        return ranking