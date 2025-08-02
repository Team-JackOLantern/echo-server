from typing import Dict, List

# 욕설 패턴 정의
PROFANITY_PATTERNS = {
    1: ["씨발", "시발", "병신", "좆", "썅", "개새끼", "씨팔", "시팔"],
    2: ["새끼", "지랄", "엿", "개놈", "개년", "아가리", "ㅗ", "ㅅㅂ", "짜증", "멍청", "멍청이", "바보"],
    3: ["미친", "빡치", "꺼져", "닥쳐", "ㅄ", "ㅆㅂ", "개", "죽어", "등신", "또라이"]
}

class ProfanityService:
    def __init__(self, sensitivity_level: int = 2):
        self.sensitivity_level = sensitivity_level
        self.patterns = self.get_patterns(sensitivity_level)
    
    def get_patterns(self, sensitivity: int) -> List[str]:
        """감지 레벨에 따른 패턴 반환"""
        patterns = []
        for level in range(1, sensitivity + 1):
            level_patterns = PROFANITY_PATTERNS.get(level, [])
            patterns.extend(level_patterns)
        print(f"🎯 로드된 패턴 ({sensitivity} 레벨): {len(patterns)}개")
        return patterns
    
    def set_sensitivity(self, level: int):
        """감지 레벨 변경"""
        if level in [1, 2, 3]:
            self.sensitivity_level = level
            self.patterns = self.get_patterns(level)
            return True
        return False
    
    def detect(self, text: str) -> Dict:
        """텍스트에서 욕설 감지"""
        if not text.strip():
            return {"detected": False, "pattern": None, "confidence": 0, "text": text}
        
        text_lower = text.lower()
        detected_patterns = []
        
        for pattern in self.patterns:
            if pattern.lower() in text_lower:
                detected_patterns.append(pattern)
        
        confidence = 0.8 if detected_patterns else 0
        
        if detected_patterns:
            print(f"🔴 욕설 감지: {detected_patterns}")
        
        return {
            "detected": len(detected_patterns) > 0,
            "pattern": detected_patterns[0] if detected_patterns else None,
            "patterns": detected_patterns,
            "confidence": confidence,
            "text": text
        }