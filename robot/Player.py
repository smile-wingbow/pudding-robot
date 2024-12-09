# -*- coding: utf-8 -*-
import asyncio
import subprocess
import os
import platform
import queue
import signal
import threading
import pyaudio
import numpy as np
from pydub import AudioSegment
import io

from robot import logging
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll
from contextlib import contextmanager

from . import utils

logger = logging.getLogger(__name__)


def py_error_handler(filename, line, function, err, fmt):
    pass


ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)

c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)


@contextmanager
def no_alsa_error():
    try:
        asound = cdll.LoadLibrary("libasound.so")
        asound.snd_lib_error_set_handler(c_error_handler)
        yield
        asound.snd_lib_error_set_handler(None)
    except:
        yield
        pass


def play(fname, onCompleted=None):
    player = getPlayerByFileName(fname)
    player.play(fname, onCompleted=onCompleted)


def getPlayerByFileName(fname):
    foo, ext = os.path.splitext(fname)
    if ext in [".mp3", ".wav"]:
        return SoxPlayer()


class AbstractPlayer(object):
    def __init__(self, **kwargs):
        super(AbstractPlayer, self).__init__()

    def play(self):
        pass

    def play_block(self):
        pass

    def stop(self):
        pass

    def is_playing(self):
        return False

    def join(self):
        pass


class SoxPlayer(AbstractPlayer):
    SLUG = "SoxPlayer"

    def __init__(self, **kwargs):
        super(SoxPlayer, self).__init__(**kwargs)
        self.playing = False
        self.proc = None
        self.delete = False
        self.onCompleteds = []
        # 创建一个锁用于保证同一时间只有一个音频在播放
        self.play_lock = threading.Lock()
        self.play_queue = queue.Queue()  # 播放队列
        self.play_event = threading.Event()
        self.consumer_thread = threading.Thread(target=self.playLoop)
        self.consumer_thread.start()
        self.loop = asyncio.new_event_loop()  # 创建事件循环
        self.thread_loop = threading.Thread(target=self.loop.run_forever)
        self.thread_loop.start()

        # 初始化 pyaudio
        self.p = pyaudio.PyAudio()

    def executeOnCompleted(self, res, onCompleted):
        # 全部播放完成，播放统一的 onCompleted()
        res and onCompleted and onCompleted()
        if self.play_queue.empty():
            for onCompleted in self.onCompleteds:
                onCompleted and onCompleted()

    def playLoop(self):
        while True:
            (src, onCompleted) = self.play_queue.get()
            if src:
                self.playing = True
                with self.play_lock:
                    logger.info(f"开始播放音频：{src}")
                    self.src = src
                    res = self.doPlayAudio(src)
                    self.play_queue.task_done()
                    # 将 onCompleted() 方法的调用放到事件循环的线程中执行
                    self.loop.call_soon_threadsafe(
                        self.executeOnCompleted, res, onCompleted
                    )

    def doPlayAudio(self, src, volume=20):
        system = platform.system()
        if system == "Darwin":
            cmd = ["afplay", str(src)]
        else:
            cmd = [f"play", "-v", str(volume/100), str(src)]
        logger.debug("Executing %s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.playing = True
        self.proc.wait()
        self.playing = False
        if self.delete:
            utils.check_and_delete(src)
        logger.info(f"播放完成：{src}")
        return self.proc and self.proc.returncode == 0

    def doPlay(self, audio_stream, volume=50):
        self.playing = True
        # 将 MP3 流转换为 PCM 数据
        audio_stream.seek(0)
        mp3_audio = AudioSegment.from_mp3(io.BytesIO(audio_stream.read()))

        # 导出 PCM 数据
        pcm_data = io.BytesIO()
        mp3_audio.export(pcm_data, format="raw")

        # 重置指针到流的开头
        pcm_data.seek(0)

        sample_width = mp3_audio.sample_width
        channels = mp3_audio.channels
        sample_rate = mp3_audio.frame_rate

        # 打开一个 pyaudio 播放流
        stream = self.p.open(format=self.p.get_format_from_width(sample_width),
                        channels=channels,
                        rate=sample_rate,
                        output=True)

        # 定义每次读取的块大小（字节）
        chunk_size = 1024

        try:
            # 逐块读取并播放 PCM 数据
            while True:
                data = pcm_data.read(chunk_size)
                if not data:
                    break
                # 将字节数据转换为 NumPy 数组，进行振幅调整
                audio_array = np.frombuffer(data, dtype=np.int16)  # 假设为 16 位采样
                audio_array = (audio_array * (volume / 100)).astype(np.int16)  # 音量减半

                # 将调整后的数据转换回字节格式并播放
                stream.write(audio_array.tobytes())

            logger.info("播放完成")
        except Exception as e:
            logger.error(f"播放过程中出错: {e}")
        finally:
            # 停止和关闭流
            stream.stop_stream()
            stream.close()

            self.playing = False

        return True

    def play(self, src, delete=False, onCompleted=None):
        if src and (os.path.exists(src) or src.startswith("http")):
            self.delete = delete
            self.play_queue.put((src, onCompleted))
        else:
            logger.critical(f"path not exists: {src}", stack_info=True)

    def play_sync(self, src, volume=20, delete=False):
        if src and (os.path.exists(src) or src.startswith("http")):
            self.delete = delete
            self.playing = True
            # self.play_event.clear()  # Reset the event
            # self.play_queue.put((src, self.play_event.set))
            # self.play_event.wait()  # Wait until the play is complete
            self.doPlayAudio(src, volume)
            self.playing = False
            if self.delete:
                utils.check_and_delete(src)
        else:
            logger.critical(f"path not exists: {src}", stack_info=True)

    def doPlayChunk(self, pcm_chunk, volume=50):
        pcm_data = io.BytesIO(pcm_chunk)
        sample_width = 2  # 假设使用16位PCM
        channels = 1  # 假设单声道
        sample_rate = 24000  # 假设采样率为44100Hz

        chunk_size = 4096

        stream = self.p.open(format=self.p.get_format_from_width(sample_width),
                        channels=channels,
                        rate=sample_rate,
                        output=True)

        while True:
            data = pcm_data.read(chunk_size)
            if not data:
                break

            audio_array = np.frombuffer(data, dtype=np.int16)
            audio_array = (audio_array * (volume / 100)).astype(np.int16)
            stream.write(audio_array.tobytes())

    def preappendCompleted(self, onCompleted):
        onCompleted and self.onCompleteds.insert(0, onCompleted)

    def appendOnCompleted(self, onCompleted):
        onCompleted and self.onCompleteds.append(onCompleted)

    def play_block(self):
        self.run()

    def stop(self):
        if self.proc:
            self.onCompleteds = []
            self.proc.terminate()
            self.proc.kill()
            self.proc = None
            self.playing = False
            self._clear_queue()
            # if self.delete:
            #     utils.check_and_delete(self.src)

    def is_playing(self):
        return self.playing or not self.play_queue.empty()

    def join(self):
        self.play_queue.join()

    def _clear_queue(self):
        with self.play_queue.mutex:
            self.play_queue.queue.clear()


class MusicPlayer(SoxPlayer):
    """
    给音乐播放器插件使用的，
    在 SOXPlayer 的基础上增加了列表的支持，
    并支持暂停和恢复播放
    """

    SLUG = "MusicPlayer"

    def __init__(self, playlist, plugin, **kwargs):
        super(MusicPlayer, self).__init__(**kwargs)
        self.playlist = playlist
        self.plugin = plugin
        self.idx = 0
        self.pausing = False

    def update_playlist(self, playlist):
        super().stop()
        self.playlist = playlist
        self.idx = 0
        self.play()

    def play(self):
        logger.debug("MusicPlayer play")
        path = self.playlist[self.idx]
        super().stop()
        super().play(path, False, self.next)

    def next(self):
        logger.debug("MusicPlayer next")
        super().stop()
        self.idx = (self.idx + 1) % len(self.playlist)
        self.play()

    def prev(self):
        logger.debug("MusicPlayer prev")
        super().stop()
        self.idx = (self.idx - 1) % len(self.playlist)
        self.play()

    def pause(self):
        logger.debug("MusicPlayer pause")
        self.pausing = True
        if self.proc:
            os.kill(self.proc.pid, signal.SIGSTOP)

    def stop(self):
        if self.proc:
            logger.debug(f"MusicPlayer stop {self.proc.pid}")
            self.onCompleteds = []
            os.kill(self.proc.pid, signal.SIGSTOP)
            self.proc.terminate()
            self.proc.kill()
            self.proc = None

    def resume(self):
        logger.debug("MusicPlayer resume")
        self.pausing = False
        self.onCompleteds = [self.next]
        if self.proc:
            os.kill(self.proc.pid, signal.SIGCONT)

    def is_playing(self):
        return self.playing

    def is_pausing(self):
        return self.pausing

    def turnUp(self):
        system = platform.system()
        if system == "Darwin":
            res = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                shell=False,
                capture_output=True,
                universal_newlines=True,
            )
            volume = int(res.stdout.strip())
            volume += 20
            if volume >= 100:
                volume = 100
                self.plugin.say("音量已经最大啦")
            subprocess.run(["osascript", "-e", f"set volume output volume {volume}"])
        elif system == "Linux":
            res = subprocess.run(
                ["amixer sget Master | grep 'Mono:' | awk -F'[][]' '{ print $2 }'"],
                shell=True,
                capture_output=True,
                universal_newlines=True,
            )
            if res.stdout != "" and res.stdout.strip().endswith("%"):
                volume = int(res.stdout.strip().replace("%", ""))
                volume += 20
                if volume >= 100:
                    volume = 100
                    self.plugin.say("音量已经最大啦")
                subprocess.run(["amixer", "set", "Master", f"{volume}%"])
            else:
                subprocess.run(["amixer", "set", "Master", "20%+"])
        else:
            self.plugin.say("当前系统不支持调节音量")
        self.resume()

    def turnDown(self):
        system = platform.system()
        if system == "Darwin":
            res = subprocess.run(
                ["osascript", "-e", "output volume of (get volume settings)"],
                shell=False,
                capture_output=True,
                universal_newlines=True,
            )
            volume = int(res.stdout.strip())
            volume -= 20
            if volume <= 20:
                volume = 20
                self.plugin.say("音量已经很小啦")
            subprocess.run(["osascript", "-e", f"set volume output volume {volume}"])
        elif system == "Linux":
            res = subprocess.run(
                ["amixer sget Master | grep 'Mono:' | awk -F'[][]' '{ print $2 }'"],
                shell=True,
                capture_output=True,
                universal_newlines=True,
            )
            if res.stdout != "" and res.stdout.endswith("%"):
                volume = int(res.stdout.replace("%", "").strip())
                volume -= 20
                if volume <= 20:
                    volume = 20
                    self.plugin.say("音量已经最小啦")
                subprocess.run(["amixer", "set", "Master", f"{volume}%"])
            else:
                subprocess.run(["amixer", "set", "Master", "20%-"])
        else:
            self.plugin.say("当前系统不支持调节音量")
        self.resume()
