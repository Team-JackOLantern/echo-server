import whisper
import numpy as np
import ssl
import os

# SSL 설정
ssl._create_default_https_context = ssl._create_unverified_context
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

class WhisperService:
    def __init__(self):
        self.model = None
        self.load_model()
    
    def load_model(self):
        """Whisper 모델 로드"""
        if self.model is None:
            print("🤖 Whisper 모델 로딩 중...")
            try:
                self.model = whisper.load_model("base")
                print("✅ Whisper 모델 로드 완료")
            except Exception as e:
                print(f"🔴 모델 로드 실패: {e}")
                self.model = None
    
    async def transcribe(self, audio_data: np.ndarray) -> str:
        """음성을 텍스트로 변환"""
        try:
            if self.model is None:
                return ""
            
            # 오디오 데이터 정규화
            if audio_data.max() > 1.0 or audio_data.min() < -1.0:
                audio_data = audio_data / np.max(np.abs(audio_data))
            
            audio_float32 = audio_data.astype(np.float32)
            
            # 음성인식 실행
            result = self.model.transcribe(audio_float32)
            text = result.get("text", "").strip()
            print(f"🎤 인식된 텍스트: '{text}'")
            return text
            
        except Exception as e:
            print(f"🔴 Whisper STT 오류: {e}")
            return ""