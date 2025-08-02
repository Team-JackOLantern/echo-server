import numpy as np
from typing import Any

def safe_json_convert(obj: Any) -> Any:
    """JSON 직렬화를 위한 안전한 변환"""
    if isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, (np.integer, np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: safe_json_convert(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [safe_json_convert(item) for item in obj]
    return obj

def preprocess_audio(audio_data: np.ndarray) -> np.ndarray:
    """간단한 오디오 정규화"""
    # 정규화만 수행 (-1 ~ 1 범위)
    if audio_data.max() > 1.0 or audio_data.min() < -1.0:
        audio_data = audio_data / np.max(np.abs(audio_data))
    
    return audio_data.astype(np.float32)