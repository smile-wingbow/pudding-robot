# -*- coding: utf-8 -*-
import json
import time
import cProfile
import pstats
import io
import re
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import ruamel.yaml
import sys
import pexpect
import queue
import pyaudio
import numpy as np

from snowboy import snowboydecoder

from robot.LifeCycleHandler import LifeCycleHandler
from robot.Brain import Brain
from robot.Scheduler import Scheduler
from robot.sdk import History
from robot import (
    AI,
    ASR,
    config,
    constants,
    logging,
    NLU,
    Player,
    statistic,
    TTS,
    utils,
)

from metagpt.team import Team

import asyncio
import uuid

from robot.agents.pudding_agent import Actuator, StoryBot


logger = logging.getLogger(__name__)

def run_event_loop(loop, coro):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)

class Conversation(object):

    def __init__(self, profiling=False):
        self.brain, self.asr, self.ai, self.tts, self.nlu = None, None, None, None, None
        self.reInit()
        self.scheduler = Scheduler(self)
        # 历史会话消息
        self.history = History.History()
        # 沉浸模式，处于这个模式下，被打断后将自动恢复这个技能
        self.matchPlugin = None
        self.immersiveMode = None
        self.isRecording = False
        self.profiling = profiling
        self.onSay = None
        self.onStream = None
        self.hasPardon = False
        self.player = Player.SoxPlayer()
        self.lifeCycleHandler = LifeCycleHandler(self)

        self.tts_lock = threading.Lock()
        self.play_lock = threading.Lock()


        self.is_speaking = False  # 用于标记当前是否在朗读

        self.queue = queue.Queue()  # 普通队列
        self.priority_queue = queue.Queue()  # 优先级队列
        self.lock = threading.Lock()  # 保证线程安全
        self.condition = threading.Condition(self.lock)  # 用于线程之间的同步控制
        self.tts_lock = threading.Lock()  # 保证线程安全
        self.tts_condition = threading.Condition(self.tts_lock)  # 用于线程之间的同步控制
        self.paused = False

        self.storyMode = False

        threading.Thread(target=self._process_queue, daemon=True).start()  # 启动后台线程处理队列

        self.book_id = None
        self.book_content_id = None
        self.book_content_sequence = None
        self.book_content_text_id = None
        self.book_content_text_sequence = None

        self.nickname = "小圆"
        self.playmate = "小布"


        self.team = Team()
        self.team.hire(
            [
                Actuator(self.nickname, self.playmate, self.say, self.say_sync, self.say_with_priority, self.activeListen, self.resume, self.setStoryMode, self.clearQueue, self.set_book_id, self.set_book_content_id, self.set_book_content_sequence, self.set_book_content_text_id, self.set_book_content_text_sequence),
                StoryBot(self.nickname, self.playmate),
            ]
        )

        def save_mac_to_yaml(mac_address, filename="setting.store"):
            """保存MAC地址到YAML文件"""
            yaml = ruamel.yaml.YAML()
            file_path = os.path.join(os.getcwd(), filename)

            # 尝试加载现有内容
            try:
                with open(file_path, "r") as file:
                    data = yaml.load(file)
            except FileNotFoundError:
                data = {}

            # 检查设备列表是否存在，不存在则创建
            if "bluetooth_devices" not in data:
                data["bluetooth_devices"] = []

            # 如果MAC地址不存在，则添加
            if mac_address not in data["bluetooth_devices"]:
                data["bluetooth_devices"].append(mac_address)
                with open(file_path, "w") as file:
                    yaml.dump(data, file)
                print(f"MAC address {mac_address} saved to {filename}")
            else:
                print(f"MAC address {mac_address} already exists in {filename}")

        def load_mac_from_yaml(filename="setting.store"):
            """从YAML文件中加载已保存的MAC地址"""
            yaml = ruamel.yaml.YAML()
            file_path = os.path.join(os.getcwd(), filename)

            try:
                with open(file_path, "r") as file:
                    data = yaml.load(file)
                return data.get("bluetooth_devices", [])
            except FileNotFoundError:
                return []

        def execute_command(command):
            print(f"Executing command: {command}")
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate()
            stdout_decoded = stdout.decode('utf-8')
            stderr_decoded = stderr.decode('utf-8')

            print(f"Command output:\n{stdout_decoded}")
            if stderr_decoded:
                print(f"Command error:\n{stderr_decoded}")

            return stdout_decoded, stderr_decoded

        def connect_and_set_profile(mac_address):
            """无限重试连接蓝牙设备并设置音频配置文件，直到成功为止"""
            while True:
                connect_command = f"bluetoothctl connect {mac_address}"
                stdout, _ = execute_command(connect_command)
                if "Connection successful" in stdout:
                    print(f"Connection successful to {mac_address}")

                    profile_command = f"pactl set-card-profile bluez_card.{mac_address.replace(':', '_')} handsfree_head_unit"
                    stdout, stderr = execute_command(profile_command)
                    if stdout or stderr:
                        raise Exception(f"Failed to set profile for {mac_address}: {stderr}")
                    print(f"Profile set successfully for {mac_address}")
                    return

                print(f"Failed to connect to {mac_address}. Retrying in 10 seconds...")
                time.sleep(10)

        def scan_and_pair_device(target_keyword):
            try:
                print("Starting bluetoothctl process...")
                btctl = pexpect.spawn("bluetoothctl", encoding='utf-8', maxread=4096, searchwindowsize=4096, timeout=30)
                btctl.logfile_read = sys.stdout
                btctl.expect("#")  # 等待命令提示符
                print("Entering 'scan on' mode...")
                btctl.sendline("scan on")

                while True:
                    line = btctl.readline().strip()  # 逐行读取输出
                    if line:  # 如果读取到输出
                        print(f"Captured line: {line}")

                        # 查找包含目标关键词的行
                        if target_keyword in line:

                            print(f"Found target keyword in output: {line}")

                            match = re.search('Device\\s([0-9A-F:]{17}).*' + re.escape(target_keyword), line)
                            if match:
                                mac_address = match.group(1)
                                print(f"MAC Address: {mac_address}")

                                # 执行配对
                                btctl.sendline(f"pair {mac_address}")
                                pair_index = btctl.expect([pexpect.TIMEOUT, pexpect.EOF, "Pairing successful"], timeout=30)
                                if pair_index == 2:  # 配对成功
                                    print("Pairing successful")
                                    save_mac_to_yaml(mac_address)

                                    # 执行信任
                                    btctl.sendline(f"trust {mac_address}")
                                    pair_index = btctl.expect([pexpect.TIMEOUT, pexpect.EOF, "trust successful"],
                                                              timeout=30)
                                    if pair_index == 2:  # 配对成功
                                        print("Trust successful")

                                    # 执行连接
                                    connect_attempts = 0
                                    while connect_attempts < 3:
                                        btctl.sendline(f"connect {mac_address}")
                                        connect_index = btctl.expect(
                                            [pexpect.TIMEOUT, pexpect.EOF, "Connection successful"], timeout=30)
                                        if connect_index == 2:  # 连接成功
                                            print("Connection successful")
                                            btctl.sendline("exit")
                                            return mac_address
                                        else:
                                            print(f"Connection attempt {connect_attempts + 1} failed, retrying...")
                                            connect_attempts += 1
                                    raise Exception("Failed to connect after 3 attempts")
                    else:
                        print("Timeout or unexpected end of output. Restarting scan...")
                        btctl.sendline("scan off")
                        time.sleep(20)
                        btctl.sendline("scan on")
            except Exception as e:
                btctl.close()
                raise e

        def bluetooth_thread():
            stored_macs = load_mac_from_yaml()
            if stored_macs:
                for mac in stored_macs:
                    try:
                        connect_and_set_profile(mac)
                        print("Connected and profile set successfully from stored MAC address.")
                        return  # 如果成功，结束线程
                    except Exception as e:
                        print(f"Failed to connect using stored MAC address {mac}: {e}")
            else:
                print("No stored MAC addresses found, proceeding with scan and pair.")

            target_keyword = "联想Thinkplus-K3 pro"
            try:
                scan_and_pair_device(target_keyword)
            except Exception as e:
                print(f"Error: {e}")

        # 创建并启动线程
        bt_thread = threading.Thread(target=bluetooth_thread)
        bt_thread.start()
        bt_thread.join()


    def pause(self):
        """暂停文本转语音任务"""
        with self.lock:
            self.paused = True
        with self.tts_lock:
            self.is_speaking = False

    def resume(self):
        """恢复文本转语音任务"""
        with self.lock:
            self.paused = False
            self.condition.notify_all()  # 通知所有等待的线程继续执行
        with self.tts_lock:
            self.is_speaking = False
            self.tts_condition.notify_all()

    def clearQueue(self):
        while not self.queue.empty():
            self.queue.get()

    def set_book_id(self, book_id):
        self.book_id = book_id

    def set_book_content_id(self, book_content_id):
        self.book_content_id = book_content_id

    def set_book_content_sequence(self, book_content_sequence):
        self.book_content_sequence = book_content_sequence

    def set_book_content_text_id(self, book_content_text_id):
        self.book_content_text_id = book_content_text_id

    def set_book_content_text_sequence(self, bookt_content_text_sequence):
        self.book_content_text_sequence = bookt_content_text_sequence

    def _lastCompleted(self, index, onCompleted):
        if index >= self.tts_count - 1:
            # logger.debug(f"执行onCompleted")
            onCompleted and onCompleted()
        with self.lock:
            self.condition.notify()  # 通知下一个音频可以开始

    def _play_silence(self, duration=2):
        # 初始化 PyAudio
        p = pyaudio.PyAudio()

        # 设置流参数
        volume = 0.0  # 静音
        sample_rate = 44100  # 采样率
        samples = np.zeros(int(sample_rate * duration), dtype=np.float32)  # 生成静音数据

        # 创建流
        stream = p.open(format=pyaudio.paFloat32,
                        channels=1,
                        rate=sample_rate,
                        output=True)

        # 播放静音
        stream.write(samples.tobytes())

        # 关闭流
        stream.stop_stream()
        stream.close()
        p.terminate()

    async def stream_and_play(self, phrase, silent, speed_ratio, emotion, character_category, volume=50, cache=False):
        pcm_chunks = []  # 用于收集所有的音频数据块
        async for audio_chunk in self.tts.get_speech_ws_stream(phrase, silent, speed_ratio, emotion, character_category):
            pcm_chunks.append(audio_chunk)  # 收集 PCM 数据块
            # 实时播放每块数据
            self.player.doPlayChunk(audio_chunk, volume)
        if cache:
            # 当所有数据块播放完毕后，保存音频缓存
            await utils.saveWsStreamVoiceCache(pcm_chunks, phrase)

    def _ttsAction(self, msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, index, onCompleted=None):
        if msg:
            voice = ""
            if utils.getCache(msg):
                logger.info(f"第{index}段TTS命中缓存，播放缓存语音")
                # 获取缓存音频文件，使用asyncio调用异步方法
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                voice = loop.run_until_complete(utils.getCache_async(msg))
                loop.close()

                logger.info(f"即将播放第{index}段TTS。msg: {msg}")
                self.player.play_sync(voice, volume, not cache)
                self._play_silence(duration=cache_play_silence_duration)
                if onCompleted:
                    loop = asyncio.get_event_loop()
                    if asyncio.iscoroutinefunction(onCompleted):
                        # 使用 run_coroutine_threadsafe 调度协程
                        asyncio.run_coroutine_threadsafe(onCompleted(), loop)
                    else:
                        # 直接调用普通函数
                        onCompleted()
                return voice
            else:
                try:
                    loop = asyncio.get_running_loop()  # 检查是否有事件循环
                except RuntimeError:
                    loop = asyncio.new_event_loop()  # 创建一个新的事件循环
                    asyncio.set_event_loop(loop)

                # 运行异步 stream_and_play 方法
                loop.run_until_complete(
                    self.stream_and_play(msg, tts_silent, speed_ratio, emotion, character_category, volume, cache))

                if onCompleted:
                    loop = asyncio.get_event_loop()
                    if asyncio.iscoroutinefunction(onCompleted):
                        # 使用 run_coroutine_threadsafe 调度协程
                        asyncio.run_coroutine_threadsafe(onCompleted(), loop)
                    else:
                        # 直接调用普通函数
                        onCompleted()

                # 获取http语音
                # voice = self.tts.get_speech_http(msg, tts_silent, speed_ratio, emotion, character_category)
                #
                # logger.info(f"即将播放第{index}段TTS。msg: {msg}")
                # self.player.play_sync(voice, volume, not cache)
                # # self._play_silence(duration=cache_play_silence_duration)
                # if onCompleted:
                #     loop = asyncio.get_event_loop()
                #     if asyncio.iscoroutinefunction(onCompleted):
                #         # 使用 run_coroutine_threadsafe 调度协程
                #         asyncio.run_coroutine_threadsafe(onCompleted(), loop)
                #     else:
                #         # 直接调用普通函数
                #         onCompleted()
                # # 缓存音频流
                # if cache:
                #     utils.saveHttpVoiceCache(voice, volume, msg)
                #
                # utils.check_and_delete(voice)
                # return voice

                # 以下为获取websokcet语音的方式
                # try:
                #     # 获取TTS生成的语音流
                #     loop = asyncio.new_event_loop()
                #     asyncio.set_event_loop(loop)
                #     voice_stream = loop.run_until_complete(self.tts.get_speech_ws(msg, tts_silent, speed_ratio, emotion, character_category, operation="query"))
                #     loop.close()
                #
                #     logger.info(f"第{index}段TTS合成成功。msg: {msg}")
                #     logger.info(f"即将播放第{index}段TTS。msg: {msg}")
                #     self.player.doPlay(voice_stream, volume)
                #     if onCompleted:
                #         loop = asyncio.get_event_loop()
                #         if asyncio.iscoroutinefunction(onCompleted):
                #             # 使用 run_coroutine_threadsafe 调度协程
                #             asyncio.run_coroutine_threadsafe(onCompleted(), loop)
                #         else:
                #             # 直接调用普通函数
                #             onCompleted()
                #     # 缓存音频流
                #     if cache:
                #         utils.saveWsVoiceCache(voice_stream, volume, msg)
                #
                #     return voice_stream
                # except Exception as e:
                #     logger.error(f"语音合成失败：{e}", stack_info=True)
                #     return None

    def _ttsAction_sync(self, msg, cache, index):
        if msg:
            voice = ""
            if utils.getCache(msg):
                logger.info(f"第{index}段TTS命中缓存，播放缓存语音")
                voice = utils.getCache(msg)

                with self.play_lock:
                    self.player.play_sync(
                        voice,
                        not cache
                    )
                return voice
            else:
                try:
                    voice = self.tts.get_speech_http(msg)
                    logger.info(f"第{index}段TTS合成成功。msg: {msg}")

                    with self.play_lock:
                        logger.info(f"即将播放第{index}段TTS。msg: {msg}")
                        self.player.play_sync(
                            voice,
                            not cache
                        )
                    return voice
                except Exception as e:
                    logger.error(f"语音合成失败：{e}", stack_info=True)
                    traceback.print_exc()
                    return None

    def getHistory(self):
        return self.history

    # 设置绘本模式
    def setStoryMode(self, storyMode):
        self.storyMode = storyMode

    def interrupt(self):
        # 在故事模式下，需要暂停文本转语音队列的处理
        # 停止当前的缓存语音播放
        if self.player:
            self.player.stop()
        # 停止语音转文本的websocket流
        if self.tts:
            self.tts.stop_websocket_stream()
        if self.storyMode:
            self.pause()
        # if self.immersiveMode:
        #     self.brain.pause()

    def reInit(self):
        """重新初始化"""
        try:
            # self.asr = ASR.get_engine_by_slug(config.get("asr_engine", "tencent-asr"))
            self.asr = ASR.get_engine_by_slug(config.get("asr_engine", "volc-asr"))
        except Exception as e:
            logger.info(f"对话初始化失败：{e}", stack_info=True)
        try:
            self.ai = AI.get_robot_by_slug(config.get("robot", "tuling"))
            self.tts = TTS.get_engine_by_slug(config.get("tts_engine", "baidu-tts"))
            self.nlu = NLU.get_engine_by_slug(config.get("nlu_engine", "unit"))
            self.player = Player.SoxPlayer()
            self.brain = Brain(self)
            self.brain.printPlugins()

            logger.info(f"self.asr: {self.asr}")
            logger.info(f"self.tts: {self.tts}")
        except Exception as e:
            logger.critical(f"对话初始化失败：{e}", stack_info=True)

    def checkRestore(self):
        if self.immersiveMode:
            logger.info("处于沉浸模式，恢复技能")
            self.lifeCycleHandler.onRestore()
            self.brain.restore()

    def _InGossip(self, query):
        return self.immersiveMode in ["Gossip"] and not "闲聊" in query

    def doResponse(self, query, UUID="", onSay=None, onStream=None):
        """
        响应指令

        :param query: 指令
        :UUID: 指令的UUID
        :onSay: 朗读时的回调
        :onStream: 流式输出时的回调
        """
        statistic.report(1)
        self.interrupt()
        self.appendHistory(0, query, UUID)

        if onSay:
            self.onSay = onSay

        if onStream:
            self.onStream = onStream

        if query.strip() == "" and not self.storyMode:
            self.pardon()
            return

        lastImmersiveMode = self.immersiveMode

        context = {
            "user_input": query,
            "book_id": self.book_id,
            "book_content_id": self.book_content_id,
            "book_content_sequence": self.book_content_sequence,
            "book_content_text_id": self.book_content_text_id,
            "book_content_text_sequence": self.book_content_text_sequence,
        }

        context_str = json.dumps(context, ensure_ascii=False)

        self.team.invest(investment=100)
        self.team.run_project(context_str)

        event_loop = asyncio.new_event_loop()

        t = threading.Thread(target=run_event_loop, args=(event_loop, self.team.run(n_round=20)))
        t.start()

    def doParse(self, query):
        args = {
            "service_id": config.get("/unit/service_id", "S13442"),
            "api_key": config.get("/unit/api_key", "w5v7gUV3iPGsGntcM84PtOOM"),
            "secret_key": config.get(
                "/unit/secret_key", "KffXwW6E1alcGplcabcNs63Li6GvvnfL"
            ),
        }
        return self.nlu.parse(query, **args)

    def setImmersiveMode(self, slug):
        self.immersiveMode = slug

    def getImmersiveMode(self):
        return self.immersiveMode

    def converse(self, fp, callback=None):
        """核心对话逻辑"""
        logger.info("结束录音")
        self.lifeCycleHandler.onThink()
        self.isRecording = False
        if self.profiling:
            logger.info("性能调试已打开")
            pr = cProfile.Profile()
            pr.enable()
            self.doConverse(fp, callback)
            pr.disable()
            s = io.StringIO()
            sortby = "cumulative"
            ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
            ps.print_stats()
            print(s.getvalue())
        else:
            self.doConverse(fp, callback)

    def doConverse(self, fp, callback=None, onSay=None, onStream=None):
        self.interrupt()
        try:
            query = self.asr.transcribe(fp)
            logger.info(f"doConverse query-------:{query}")
        except Exception as e:
            logger.critical(f"ASR识别失败：{e}", stack_info=True)
            traceback.print_exc()
        utils.check_and_delete(fp)
        try:
            self.doResponse(query, callback, onSay, onStream)
        except Exception as e:
            logger.critical(f"回复失败：{e}", stack_info=True)
            traceback.print_exc()
        utils.clean()

    def appendHistory(self, t, text, UUID="", plugin=""):
        """将会话历史加进历史记录"""
        if t in (0, 1) and text:
            if text.endswith(",") or text.endswith("，"):
                text = text[:-1]
            if UUID == "" or UUID == None or UUID == "null":
                UUID = str(uuid.uuid1())
            # 将图片处理成HTML
            pattern = r"https?://.+\.(?:png|jpg|jpeg|bmp|gif|JPG|PNG|JPEG|BMP|GIF)"
            url_pattern = r"^https?://.+"
            imgs = re.findall(pattern, text)
            for img in imgs:
                text = text.replace(
                    img,
                    f'<a data-fancybox="images" href="{img}"><img src={img} class="img fancybox"></img></a>',
                )
            urls = re.findall(url_pattern, text)
            for url in urls:
                text = text.replace(url, f'<a href={url} target="_blank">{url}</a>')
            self.lifeCycleHandler.onResponse(t, text)
            self.history.add_message(
                {
                    "type": t,
                    "text": text,
                    "time": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(time.time())
                    ),
                    "uuid": UUID,
                    "plugin": plugin,
                }
            )

    def _onCompleted(self, msg):
        pass

    def pardon(self):
        if not self.hasPardon:
            self.say("抱歉，刚刚没听清，能再说一遍吗？", volume=50, character_category=-1, cache=True)
            self.hasPardon = True
        else:
            self.say("没听清呢", volume=50, character_category=-1, cache=True)
            self.hasPardon = False

    def _tts_line(self, line, cache, index=0, onCompleted=None):
        """
        对单行字符串进行 TTS 并返回合成后的音频
        :param line: 字符串
        :param cache: 是否缓存 TTS 结果
        :param index: 合成序号
        :param onCompleted: 播放完成的操作
        """
        line = line.strip()
        pattern = r"http[s]?://.+"
        if re.match(pattern, line):
            logger.info("内容包含URL，屏蔽后续内容")
            return None
        line.replace("- ", "")
        if line:
            result = self._ttsAction(line, -1, cache, index, onCompleted)
            return result
        return None

    def _tts(self, msg, volume, silent, speed_ratio, emotion, character_category, cache, onCompleted=None):
        """
        对字符串进行 TTS 并返回合成后的音频
        :param lines: 字符串列表
        :param cache: 是否缓存 TTS 结果
        """
        audios = []
        pattern = r"http[s]?://.+"
        logger.info("_tts")
        with self.tts_lock:
            with ThreadPoolExecutor(max_workers=5) as pool:
                all_task = []
                index = 0
                if re.match(pattern, msg):
                    logger.info("内容包含URL，屏蔽后续内容")
                    self.tts_count -= 1
                if msg:
                    task = pool.submit(
                        self._ttsAction, msg.strip(), volume, silent, speed_ratio, emotion, character_category, cache, index, onCompleted
                    )
                    index += 1
                    all_task.append(task)
                else:
                    self.tts_count -= 1
                for future in as_completed(all_task):
                    audio = future.result()
                    if audio:
                        audios.append(audio)
            return audios

    def _tts_sync(self, lines, cache):
        """
        对字符串进行 TTS 并返回合成后的音频，同步播放
        :param lines: 字符串列表
        :param cache: 是否缓存 TTS 结果
        """
        audios = []
        pattern = r"http[s]?://.+"
        logger.info("_tts")
        with self.tts_lock:
            with ThreadPoolExecutor(max_workers=5) as pool:
                all_task = []
                index = 0
                for line in lines:
                    if re.match(pattern, line):
                        logger.info("内容包含URL，屏蔽后续内容")
                        self.tts_count -= 1
                        continue
                    if line:
                        task = pool.submit(
                            self._ttsAction_sync, line.strip(), cache, index
                        )
                        index += 1
                        all_task.append(task)
                    else:
                        self.tts_count -= 1
                for future in as_completed(all_task):
                    audio = future.result()
                    if audio:
                        audios.append(audio)
            return audios

    def _after_play(self, msg, audios, plugin=""):
        pass
        # cached_audios = [
        #     f"http://{config.get('/server/host')}:{config.get('/server/port')}/audio/{os.path.basename(voice)}"
        #     for voice in audios
        # ]
        # if self.onSay:
        #     logger.info(f"onSay: {msg}, {cached_audios}")
        #     self.onSay(msg, cached_audios, plugin=plugin)
        #     self.onSay = None
        # utils.lruCache()  # 清理缓存

    def stream_say(self, stream, cache=False, onCompleted=None):
        """
        从流中逐字逐句生成语音
        :param stream: 文字流，可迭代对象
        :param cache: 是否缓存 TTS 结果
        :param onCompleted: 声音播报完成后的回调
        """
        lines = []
        line = ""
        resp_uuid = str(uuid.uuid1())
        audios = []
        if onCompleted is None:
            onCompleted = lambda: self._onCompleted(msg)
        self.tts_count = 0
        index = 0
        skip_tts = False
        for data in stream():
            if self.onStream:
                self.onStream(data, resp_uuid)
            line += data
            if any(char in data for char in utils.getPunctuations()):
                if "```" in line.strip():
                    skip_tts = True
                if not skip_tts:
                    audio = self._tts_line(line.strip(), cache, index, onCompleted)
                    if audio:
                        self.tts_count += 1
                        audios.append(audio)
                        index += 1
                else:
                    logger.info(f"{line} 属于代码段，跳过朗读")
                lines.append(line)
                line = ""
        if line.strip():
            lines.append(line)
        if skip_tts:
            self._tts_line("内容包含代码，我就不念了", True, index, onCompleted)
        msg = "".join(lines)
        self.appendHistory(1, msg, UUID=resp_uuid, plugin="")
        self._after_play(msg, audios, "")

    def _process_queue(self):
        """
        循环处理队列中的任务
        """
        while True:
            try:
                with self.lock:
                    while self.paused:  # 如果暂停，则等待
                        self.condition.wait()  # 在这里等待
                # task = None
                if not self.priority_queue.empty():
                    task = self.priority_queue.get()
                # 如果优先级队列为空，检查普通队列
                elif not self.queue.empty():
                    task = self.queue.get()
                else:
                    time.sleep(0.01)  # 如果两个队列都为空，休眠防止占用过多CPU资源
                    continue

                # task = self.queue.get()  # 从队列中取出一个任务
                if task is None:
                    break
                if not self.is_speaking:
                    # clearBook是一个标志位，在播放完绘本的最后一句后传入，退出绘本故事模式，清空绘本id等设置
                    msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, plugin, onCompleted, append_history, clearBook = task
                    if not clearBook:
                        with self.tts_lock:
                            self.is_speaking = True
                            logger.info("开始处理语音...")
                            # 调用 _process_say 来执行语音处理
                        try:
                            self._process_say(msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, plugin,
                                                  onCompleted, append_history)
                        finally:
                            with self.tts_lock:
                                self.is_speaking = False
                                self.tts_condition.notify()  # 确保通知在最后
                            # 语音播放结束后，通知下一个语音可以开始处理
                        with self.tts_lock:
                            self.is_speaking = False
                            self.tts_condition.notify()
                    else:
                        self.setStoryMode(False)
                        self.set_book_id(None)
                        self.set_book_content_id(None)
                        self.set_book_content_sequence(None)
                        self.set_book_content_text_id(None)
                        self.set_book_content_text_sequence(None)
                else:
                    logger.info("正在处理语音，跳过新任务")
            except Exception as e:
                print(f"Error in processing queue: {e}")  # 输出错误信息

    def _process_say(self, msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, plugin, onCompleted,
                     append_history):
        """
        处理朗读逻辑，将文本转换为语音并播放
        """
        # 省略历史记录逻辑，处理文本后
        if not msg:
            return

        logger.info(f"即将朗读语音：{msg}")

        # 如果 onCompleted 是 None，默认设置为 self._onCompleted
        if onCompleted is None:
            onCompleted = lambda: self._onCompleted(msg)

        # 获取 TTS 语音文件或缓存
        audios = self._ttsAction(msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, 0, onCompleted)


        self._after_play(msg, audios, plugin)

    def say(self, msg, volume, tts_silent=125, cache_play_silence_duration=2, speed_ratio=1.0, emotion="happy", character_category=-1, cache=False, plugin="", onCompleted=None, append_history=True, clearBook=False):
        """
        将文本加入朗读队列
        """
        # 将任务放入队列
        self.queue.put((msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, plugin, onCompleted, append_history, clearBook))

    def say_with_priority(self, msg, volume, tts_silent=125, cache_play_silence_duration=2, speed_ratio=1.0, emotion="happy", character_category=-1, cache=False, plugin="", onCompleted=None, append_history=True, clearBook=False):
        """
       将文本加入优先朗读队列
       """
        # 将任务插入优先级队列
        self.priority_queue.put(
            (msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, plugin, onCompleted, append_history, clearBook))

    def say_sync(self, msg, volume, tts_silent=125, cache_play_silence_duration=2, speed_ratio=1.0, emotion="happy", character_category=-1, cache=False, plugin="", onCompleted=None, append_history=True):
        """
        说一句话，同步方法
        :param msg: 内容
        :param cache: 是否缓存这句话的音频
        :param plugin: 来自哪个插件的消息（将带上插件的说明）
        :param onCompleted: 完成的回调
        :param append_history: 是否要追加到聊天记录
        """
        if append_history:
            self.appendHistory(1, msg, plugin=plugin)
        msg = utils.stripPunctuation(msg).strip()

        if not msg:
            return

        logger.info(f"即将朗读语音：{msg}")
        audios = self._ttsAction(msg, volume, tts_silent, cache_play_silence_duration, speed_ratio, emotion, character_category, cache, 0, onCompleted)
        self._after_play(msg, audios, plugin)

    def activeListen(self, silent=False, silent_count_threshold=10, recording_timeout=60):
        """
        主动问一个问题(适用于多轮对话)
        :param silent: 是否不触发唤醒表现（主要用于极客模式）
        :param
        """
        if self.immersiveMode:
            self.player.stop()
        elif self.player.is_playing():
            self.player.join()  # 确保所有音频都播完
        logger.info("进入主动聆听...")
        try:
            if not silent:
                self.lifeCycleHandler.onWakeup()
            listener = snowboydecoder.ActiveListener(
                [constants.getHotwordModel(config.get("hotword", "wukong.pmdl"))]
            )
            voice = listener.listen(
                silent_count_threshold=silent_count_threshold,
                recording_timeout=recording_timeout,
            )
            if not silent:
                self.lifeCycleHandler.onThink()
            if voice:
                query = self.asr.transcribe(voice)
                utils.check_and_delete(voice)
                return query
            return ""
        except Exception as e:
            logger.error(f"主动聆听失败：{e}", stack_info=True)
            traceback.print_exc()
            return ""

    def play(self, src, delete=False, onCompleted=None, volume=1):
        """播放一个音频"""
        if self.player:
            self.interrupt()
        self.player = Player.SoxPlayer()
        self.player.play(src, delete=delete, onCompleted=onCompleted)