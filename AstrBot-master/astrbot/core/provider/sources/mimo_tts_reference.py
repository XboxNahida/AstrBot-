import hashlib
import io
import re
import wave
from pathlib import Path
from uuid import uuid4

from astrbot.core.utils.astrbot_path import get_astrbot_data_path

MAX_REFERENCE_BYTES = 7_500_000


def validate_reference(data: bytes) -> float:
    """Validate a local PCM WAV reference and return its duration.

    Args:
        data: Unencoded reference audio bytes.

    Returns:
        Audio duration in seconds.

    Raises:
        ValueError: If format, duration, or encoded size is unsupported.
    """
    if not data or len(data) > MAX_REFERENCE_BYTES:
        raise ValueError("参考音频为空或编码后超过10MB，请使用7.5MB以内的WAV")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels = audio.getnchannels()
            width = audio.getsampwidth()
            rate = audio.getframerate()
            frames = audio.getnframes()
            if channels not in (1, 2) or width not in (1, 2, 3, 4) or rate <= 0:
                raise ValueError("参考音频必须是单声道或双声道PCM WAV")
            duration = frames / rate
            if not 5 <= duration <= 30:
                raise ValueError("参考音频长度必须为5至30秒")
            if len(audio.readframes(frames)) != frames * channels * width:
                raise ValueError("参考音频数据不完整，请重新导出WAV")
    except (wave.Error, EOFError) as exc:
        raise ValueError("参考音频必须是有效的PCM WAV文件") from exc
    return duration


def read_reference(reference_id: str) -> bytes:
    """Read a content-addressed reference without accepting filesystem paths.

    Args:
        reference_id: SHA256 filename returned by store_reference.

    Returns:
        Validated reference audio bytes.

    Raises:
        ValueError: If the reference is missing, altered, or invalid.
    """
    if not re.fullmatch(r"[0-9a-f]{64}\.wav", str(reference_id)):
        raise ValueError("请选择已上传的参考音频")
    root = (Path(get_astrbot_data_path()) / "tts_voice_references").resolve()
    path = root / reference_id
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("参考音频路径无效")
    try:
        if path.stat().st_size > MAX_REFERENCE_BYTES:
            raise ValueError("参考音频编码后超过10MB")
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError("参考音频不存在或无法读取，请重新上传") from exc
    if hashlib.sha256(data).hexdigest() + ".wav" != reference_id:
        raise ValueError("参考音频校验失败，请重新上传")
    validate_reference(data)
    return data


def store_reference(data: bytes) -> str:
    """Store validated audio atomically under its content hash.

    Args:
        data: Reference audio bytes, retained locally for subsequent requests.

    Returns:
        Reference ID suitable for provider configuration.
    """
    validate_reference(data)
    root = Path(get_astrbot_data_path()) / "tts_voice_references"
    root.mkdir(parents=True, exist_ok=True)
    reference_id = hashlib.sha256(data).hexdigest() + ".wav"
    path = root / reference_id
    if path.exists():
        read_reference(reference_id)
        return reference_id
    temporary = root / f"{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return reference_id
