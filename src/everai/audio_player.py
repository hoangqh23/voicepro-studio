"""
Native Windows MP3 Audio Player using ctypes & winmm.dll (MCI - Media Control Interface).
Zero external dependencies, works out-of-the-box on all Windows systems.
"""
import ctypes
import os
import sys
from pathlib import Path
from typing import Optional, Union


class MiniAudioPlayer:
    def __init__(self, alias: str = "EverAIMiniPlayer"):
        self.alias = alias
        self.current_file: Optional[Path] = None
        self._is_playing = False
        self._is_paused = False
        self._opened = False
        self.last_error = ""
        windll = getattr(ctypes, "windll", None)
        self._winmm = getattr(windll, "winmm", None) if windll else None

    def _send_mci(self, command: str) -> Optional[str]:
        self.last_error = ""
        if not self._winmm:
            self.last_error = "Trình phát chỉ hỗ trợ Windows."
            return None
        buffer = ctypes.create_unicode_buffer(256)
        error_code = self._winmm.mciSendStringW(command, buffer, 255, 0)
        if error_code != 0:
            err_buf = ctypes.create_unicode_buffer(256)
            self._winmm.mciGetErrorStringW(error_code, err_buf, 255)
            self.last_error = err_buf.value or f"MCI {error_code}"
            return None
        return buffer.value

    def load(self, file_path: Union[str, Path]) -> bool:
        path = Path(file_path).resolve()
        if not path.is_file():
            self.last_error = "Không tìm thấy file âm thanh."
            return False
        self.stop()
        self.last_error = ""
        device_type = 'waveaudio' if path.suffix.lower() == '.wav' else 'mpegvideo'
        if self._send_mci(f'open "{path}" type {device_type} alias {self.alias}') is None:
            return False
        self._opened = True
        if self._send_mci(f"set {self.alias} time format milliseconds") is None:
            error = self.last_error
            self.stop()
            self.last_error = error
            return False
        self.current_file = path
        return True

    def start(self) -> bool:
        """Play the loaded selection, respecting a seek made before playback."""
        if not self._opened:
            return False
        position = self.get_position_ms()
        duration = self.get_duration_ms()
        if duration and position >= duration:
            position = 0
        if self._send_mci(f"play {self.alias} from {position}") is None:
            return False
        self._is_playing = True
        self._is_paused = False
        return True

    def play(self, file_path: Union[str, Path]) -> bool:
        path = Path(file_path).resolve()
        if not path.is_file():
            return False
        if not self.load(path):
            return False
        if self._send_mci(f"play {self.alias} from 0") is None:
            return False
        self._is_playing = True
        self._is_paused = False
        return True

    def pause(self) -> bool:
        if not self._is_playing or self._is_paused:
            return False
        if self._send_mci(f"pause {self.alias}") is None:
            return False
        self._is_paused = True
        return True

    def resume(self) -> bool:
        if not self._is_playing or not self._is_paused:
            return False
        if self._send_mci(f"resume {self.alias}") is None:
            return False
        self._is_paused = False
        return True

    def stop(self) -> bool:
        ok = True
        if self._opened:
            self._send_mci(f"stop {self.alias}")
            ok = self._send_mci(f"close {self.alias}") is not None
        self._opened = False
        self.current_file = None
        self._is_playing = False
        self._is_paused = False
        return ok

    def seek(self, position_ms: int) -> bool:
        if not (self._is_playing or self._opened):
            return False
        pos = max(0, int(position_ms))
        duration = self.get_duration_ms()
        if duration:
            pos = min(pos, duration)
        if self._send_mci(f"seek {self.alias} to {pos}") is None:
            return False
        if self._is_playing and not self._is_paused:
            if self._send_mci(f"play {self.alias}") is None:
                return False
        return True

    def get_position_ms(self) -> int:
        if not (self._is_playing or self._opened):
            return 0
        val = self._send_mci(f"status {self.alias} position")
        try:
            return int(val) if val else 0
        except ValueError:
            return 0

    def get_duration_ms(self) -> int:
        if not (self._is_playing or self._opened):
            return 0
        val = self._send_mci(f"status {self.alias} length")
        try:
            return int(val) if val else 0
        except ValueError:
            return 0

    def is_playing(self) -> bool:
        if not self._is_playing or self._is_paused:
            return False
        val = self._send_mci(f"status {self.alias} mode")
        return val == "playing"

    def close(self) -> None:
        self.stop()


class QtAudioPlayer:
    """QtMultimedia player used where Windows MCI is unavailable (macOS)."""

    def __init__(self, alias: str = "EverAIMiniPlayer"):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
        self._QUrl = QUrl
        self._state = QMediaPlayer.PlaybackState
        self.alias = alias
        self.current_file: Optional[Path] = None
        self.last_error = ""
        self._audio = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio)
        self._opened = False

    @property
    def _is_paused(self) -> bool:
        return self._player.playbackState() == self._state.PausedState

    def load(self, file_path: Union[str, Path]) -> bool:
        path = Path(file_path).resolve()
        if not path.is_file():
            self.last_error = "Không tìm thấy file âm thanh."
            return False
        self.stop()
        self.last_error = ""
        self._player.setSource(self._QUrl.fromLocalFile(str(path)))
        self._opened = True
        self.current_file = path
        return True

    def start(self) -> bool:
        if not self._opened:
            return False
        duration = self.get_duration_ms()
        if duration and self.get_position_ms() >= duration:
            self._player.setPosition(0)
        self._player.play()
        return True

    def play(self, file_path: Union[str, Path]) -> bool:
        if not self.load(file_path):
            return False
        self._player.setPosition(0)
        self._player.play()
        return True

    def pause(self) -> bool:
        if self._player.playbackState() != self._state.PlayingState:
            return False
        self._player.pause()
        return True

    def resume(self) -> bool:
        if self._player.playbackState() != self._state.PausedState:
            return False
        self._player.play()
        return True

    def stop(self) -> bool:
        if self._opened:
            self._player.stop()
            self._player.setSource(self._QUrl())
        self._opened = False
        self.current_file = None
        return True

    def seek(self, position_ms: int) -> bool:
        if not self._opened:
            return False
        duration = self.get_duration_ms()
        pos = max(0, int(position_ms))
        self._player.setPosition(min(pos, duration) if duration else pos)
        return True

    def get_position_ms(self) -> int:
        return int(self._player.position()) if self._opened else 0

    def get_duration_ms(self) -> int:
        return int(self._player.duration()) if self._opened else 0

    def is_playing(self) -> bool:
        return self._opened and self._player.playbackState() == self._state.PlayingState

    def close(self) -> None:
        self.stop()


if sys.platform != "win32":
    MiniAudioPlayer = QtAudioPlayer
