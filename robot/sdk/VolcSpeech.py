# coding: utf-8
#!/usr/bin/env python3


"ByteDance ASR && TTS API"
__author__ = "Wingbow"

import uuid

import hmac

import asyncio
import base64
import gzip
import json
import wave
from enum import Enum
from hashlib import sha256
from io import BytesIO
from urllib.parse import urlparse
import websockets
import copy
import requests

import time

PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

PROTOCOL_VERSION_BITS = 4
HEADER_BITS = 4
MESSAGE_TYPE_BITS = 4
MESSAGE_TYPE_SPECIFIC_FLAGS_BITS = 4
MESSAGE_SERIALIZATION_BITS = 4
MESSAGE_COMPRESSION_BITS = 4
RESERVED_BITS = 8

# Message Type:
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

# Message Type Specific Flags
NO_SEQUENCE = 0b0000  # no check sequence
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
NEG_SEQUENCE_1 = 0b0011

# Message Serialization
NO_SERIALIZATION = 0b0000
JSON = 0b0001
THRIFT = 0b0011
CUSTOM_TYPE = 0b1111

# Message Compression
NO_COMPRESSION = 0b0000
GZIP = 0b0001
CUSTOM_COMPRESSION = 0b1111

MESSAGE_TYPES = {11: "audio-only server response", 12: "frontend server response", 15: "error message from server"}
MESSAGE_TYPE_SPECIFIC_FLAGS = {0: "no sequence number", 1: "sequence number > 0",
                               2: "last message from server (seq < 0)", 3: "sequence number < 0"}
MESSAGE_SERIALIZATION_METHODS = {0: "no serialization", 1: "JSON", 15: "custom type"}
MESSAGE_COMPRESSIONS = {0: "no compression", 1: "gzip", 15: "custom compression method"}

default_header = bytearray(b'\x11\x10\x11\x00')

request_json = {
    "app": {
        "appid": "appid",
        "token": "access_token",
        "cluster": "cluster"
    },
    "user": {
        "uid": "388808087185088"
    },
    "audio": {
        "voice_type": "xxx",
        "encoding": "mp3",
        "speed_ratio": 1.0,
        "volume_ratio": 1.0,
        "pitch_ratio": 1.0,
    },
    "request": {
        "reqid": "xxx",
        "text": "字节跳动语音合成。",
        "text_type": "plain",
        "operation": "xxx",
        "silence_duration": 125,
    }
}


def generate_header(
    version=PROTOCOL_VERSION,
    message_type=CLIENT_FULL_REQUEST,
    message_type_specific_flags=NO_SEQUENCE,
    serial_method=JSON,
    compression_type=GZIP,
    reserved_data=0x00,
    extension_header=bytes()
):
    """
    protocol_version(4 bits), header_size(4 bits),
    message_type(4 bits), message_type_specific_flags(4 bits)
    serialization_method(4 bits) message_compression(4 bits)
    reserved （8bits) 保留字段
    header_extensions 扩展头(大小等于 8 * 4 * (header_size - 1) )
    """
    header = bytearray()
    header_size = int(len(extension_header) / 4) + 1
    header.append((version << 4) | header_size)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    header.extend(extension_header)
    return header


def generate_full_default_header():
    return generate_header()


def generate_audio_default_header():
    return generate_header(
        message_type=CLIENT_AUDIO_ONLY_REQUEST
    )


def generate_last_audio_default_header():
    return generate_header(
        message_type=CLIENT_AUDIO_ONLY_REQUEST,
        message_type_specific_flags=NEG_SEQUENCE
    )

def parse_response(res):
    """
    protocol_version(4 bits), header_size(4 bits),
    message_type(4 bits), message_type_specific_flags(4 bits)
    serialization_method(4 bits) message_compression(4 bits)
    reserved （8bits) 保留字段
    header_extensions 扩展头(大小等于 8 * 4 * (header_size - 1) )
    payload 类似与http 请求体
    """
    protocol_version = res[0] >> 4
    header_size = res[0] & 0x0f
    message_type = res[1] >> 4
    message_type_specific_flags = res[1] & 0x0f
    serialization_method = res[2] >> 4
    message_compression = res[2] & 0x0f
    reserved = res[3]
    header_extensions = res[4:header_size * 4]
    payload = res[header_size * 4:]
    result = {}
    payload_msg = None
    payload_size = 0
    if message_type == SERVER_FULL_RESPONSE:
        payload_size = int.from_bytes(payload[:4], "big", signed=True)
        payload_msg = payload[4:]
    elif message_type == SERVER_ACK:
        seq = int.from_bytes(payload[:4], "big", signed=True)
        result['seq'] = seq
        if len(payload) >= 8:
            payload_size = int.from_bytes(payload[4:8], "big", signed=False)
            payload_msg = payload[8:]
    elif message_type == SERVER_ERROR_RESPONSE:
        code = int.from_bytes(payload[:4], "big", signed=False)
        result['code'] = code
        payload_size = int.from_bytes(payload[4:8], "big", signed=False)
        payload_msg = payload[8:]
    if payload_msg is None:
        return result
    if message_compression == GZIP:
        payload_msg = gzip.decompress(payload_msg)
    if serialization_method == JSON:
        payload_msg = json.loads(str(payload_msg, "utf-8"))
    elif serialization_method != NO_SERIALIZATION:
        payload_msg = str(payload_msg, "utf-8")
    result['payload_msg'] = payload_msg
    result['payload_size'] = payload_size
    return result


def read_wav_info(data: bytes = None) -> (int, int, int, int, int):
    with BytesIO(data) as _f:
        wave_fp = wave.open(_f, 'rb')
        nchannels, sampwidth, framerate, nframes = wave_fp.getparams()[:4]
        wave_bytes = wave_fp.readframes(nframes)
    return nchannels, sampwidth, framerate, nframes, len(wave_bytes)

class AudioType(Enum):
    LOCAL = 1  # 使用本地音频文件

class AsrWsClient:
    def __init__(self, audio_path, cluster, **kwargs):
        """
        :param config: config
        """
        self.audio_path = audio_path
        self.cluster = cluster
        self.success_code = 1000  # success code, default is 1000
        self.seg_duration = int(kwargs.get("seg_duration", 15000))
        self.nbest = int(kwargs.get("nbest", 1))
        self.appid = kwargs.get("appid", "")
        self.token = kwargs.get("token", "")
        self.ws_url = kwargs.get("ws_url", "wss://openspeech.bytedance.com/api/v2/asr")
        self.uid = kwargs.get("uid", "pudding-robot")
        self.workflow = kwargs.get("workflow", "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate")
        self.show_language = kwargs.get("show_language", False)
        self.show_utterances = kwargs.get("show_utterances", False)
        self.result_type = kwargs.get("result_type", "full")
        self.format = kwargs.get("format", "wav")
        self.rate = kwargs.get("sample_rate", 16000)
        self.language = kwargs.get("language", "zh-CN")
        self.bits = kwargs.get("bits", 16)
        self.channel = kwargs.get("channel", 1)
        self.codec = kwargs.get("codec", "raw")
        self.audio_type = kwargs.get("audio_type", AudioType.LOCAL)
        self.secret = kwargs.get("secret", "access_secret")
        self.auth_method = kwargs.get("auth_method", "token")
        self.mp3_seg_size = int(kwargs.get("mp3_seg_size", 10000))

    def construct_request(self, reqid):
        req = {
            'app': {
                'appid': self.appid,
                'cluster': self.cluster,
                'token': self.token,
            },
            'user': {
                'uid': self.uid
            },
            'request': {
                'reqid': reqid,
                'nbest': self.nbest,
                'workflow': self.workflow,
                'show_language': self.show_language,
                'show_utterances': self.show_utterances,
                'result_type': self.result_type,
                "sequence": 1
            },
            'audio': {
                'format': self.format,
                'rate': self.rate,
                'language': self.language,
                'bits': self.bits,
                'channel': self.channel,
                'codec': self.codec
            }
        }
        return req

    @staticmethod
    def slice_data(data: bytes, chunk_size: int) -> (list, bool):
        """
        slice data
        :param data: wav data
        :param chunk_size: the segment size in one request
        :return: segment data, last flag
        """
        data_len = len(data)
        offset = 0
        while offset + chunk_size < data_len:
            yield data[offset: offset + chunk_size], False
            offset += chunk_size
        else:
            yield data[offset: data_len], True

    def _real_processor(self, request_params: dict) -> dict:
        pass

    def token_auth(self):
        return {'Authorization': 'Bearer; {}'.format(self.token)}

    def signature_auth(self, data):
        header_dicts = {
            'Custom': 'auth_custom',
        }

        url_parse = urlparse(self.ws_url)
        input_str = 'GET {} HTTP/1.1\n'.format(url_parse.path)
        auth_headers = 'Custom'
        for header in auth_headers.split(','):
            input_str += '{}\n'.format(header_dicts[header])
        input_data = bytearray(input_str, 'utf-8')
        input_data += data
        mac = base64.urlsafe_b64encode(
            hmac.new(self.secret.encode('utf-8'), input_data, digestmod=sha256).digest())
        header_dicts['Authorization'] = 'HMAC256; access_token="{}"; mac="{}"; h="{}"'.format(self.token,
                                                                                              str(mac, 'utf-8'), auth_headers)
        return header_dicts

    async def segment_data_processor(self, wav_data: bytes, segment_size: int):
        reqid = str(uuid.uuid4())
        # 构建 full client request，并序列化压缩
        request_params = self.construct_request(reqid)
        payload_bytes = str.encode(json.dumps(request_params))
        payload_bytes = gzip.compress(payload_bytes)
        full_client_request = bytearray(generate_full_default_header())
        full_client_request.extend((len(payload_bytes)).to_bytes(4, 'big'))  # payload size(4 bytes)
        full_client_request.extend(payload_bytes)  # payload
        header = None
        if self.auth_method == "token":
            header = self.token_auth()
        elif self.auth_method == "signature":
            header = self.signature_auth(full_client_request)
        async with websockets.connect(self.ws_url, extra_headers=header, max_size=1000000000) as ws:
            # 发送 full client request
            await ws.send(full_client_request)
            res = await ws.recv()
            result = parse_response(res)
            if 'payload_msg' in result and result['payload_msg']['code'] != self.success_code:
                return result
            for seq, (chunk, last) in enumerate(AsrWsClient.slice_data(wav_data, segment_size), 1):
                # if no compression, comment this line
                payload_bytes = gzip.compress(chunk)
                audio_only_request = bytearray(generate_audio_default_header())
                if last:
                    audio_only_request = bytearray(generate_last_audio_default_header())
                audio_only_request.extend((len(payload_bytes)).to_bytes(4, 'big'))  # payload size(4 bytes)
                audio_only_request.extend(payload_bytes)  # payload
                # 发送 audio-only client request
                await ws.send(audio_only_request)
                res = await ws.recv()
                result = parse_response(res)
                if 'payload_msg' in result and result['payload_msg']['code'] != self.success_code:
                    return result
        return result

    async def execute(self):
        with open(self.audio_path, mode="rb") as _f:
            data = _f.read()
        audio_data = bytes(data)
        if self.format == "mp3":
            segment_size = self.mp3_seg_size
            return await self.segment_data_processor(audio_data, segment_size)
        if self.format != "wav":
            raise Exception("format should in wav or mp3")
        nchannels, sampwidth, framerate, nframes, wav_len = read_wav_info(
            audio_data)
        size_per_sec = nchannels * sampwidth * framerate
        segment_size = int(size_per_sec * self.seg_duration / 1000)
        return await self.segment_data_processor(audio_data, segment_size)


# 字节跳动火山引擎TTS请求
class volcSpeech(object):
    __slots__ = (
        "APPID",
        "TOKEN",
        "CLUSTER",
        "ASR_CLUSTER",
        "VOICE_TYPE",
        "HOST",
        "API_URL_WS",
        "API_URL_HTTP",
        "STOP"
    )

    def __init__(self, APPID, TOKEN, CLUSTER, ASR_CLUSTER, VOICE_TYPE, HOST, API_URL_WS, API_URL_HTTP):
        self.APPID, self.TOKEN, self.CLUSTER, self.ASR_CLUSTER, self.VOICE_TYPE, self.HOST, self.API_URL_WS, self.API_URL_HTTP = APPID, TOKEN, CLUSTER, ASR_CLUSTER, VOICE_TYPE, HOST, API_URL_WS, API_URL_HTTP
        self.STOP = False

    @property
    def appid(self):
        return self.APPID

    @appid.setter
    def appid(self, APPID):
        if not isinstance(APPID, str):
            raise ValueError("AppId must be a string!")
        if len(APPID) == 0:
            raise ValueError("AppId can not be empty!")
        self.APPID = APPID

    @property
    def token(self):
        return self.TOKEN

    @token.setter
    def token(self, TOKEN):
        if not isinstance(TOKEN, str):
            raise ValueError("token must be a string!")
        if len(TOKEN) == 0:
            raise ValueError("token can not be empty!")
        self.TOKEN = TOKEN

    @property
    def cluster(self):
        return self.CLUSTER

    @cluster.setter
    def cluster(self, CLUSTER):
        if not isinstance(CLUSTER, str):
            raise ValueError("cluster must be a string!")
        if len(CLUSTER) == 0:
            raise ValueError("cluster can not be empty!")
        self.CLUSTER = CLUSTER

    @property
    def asr_cluster(self):
        return self.ASR_CLUSTER

    @asr_cluster.setter
    def asr_cluster(self, ASR_CLUSTER):
        if not isinstance(ASR_CLUSTER, str):
            raise ValueError("asr_cluster must be a string!")
        if len(ASR_CLUSTER) == 0:
            raise ValueError("asr_cluster can not be empty!")
        self.ASR_CLUSTER = ASR_CLUSTER

    @property
    def voice_type(self):
        return self.VOICE_TYPE

    @voice_type.setter
    def voice_type(self, VOICE_TYPE):
        self.VOICE_TYPE = VOICE_TYPE

    @property
    def host(self):
        return self.HOST

    @host.setter
    def host(self, HOST):
        if not isinstance(HOST, str):
            raise ValueError("host must be an string!")
        if len(HOST) == 0:
            raise ValueError("host can not be empty!")
        self.HOST = HOST

    @property
    def api_url_ws(self):
        return self.API_URL_WS

    @api_url_ws.setter
    def api_url_ws(self, API_URL_WS):
        if not isinstance(API_URL_WS, str):
            raise ValueError("api_url_ws must be an string!")
        if len(API_URL_WS) == 0:
            raise ValueError("api_url_ws can not be empty!")
        self.API_URL_WS = API_URL_WS

    @property
    def api_url_http(self):
        return self.API_URL_HTTP

    @api_url_http.setter
    def api_url_http(self, API_URL_HTTP):
        if not isinstance(API_URL_HTTP, str):
            raise ValueError("api_url_http must be an string!")
        if len(API_URL_HTTP) == 0:
            raise ValueError("api_url_http can not be empty!")
        self.API_URL_HTTP = API_URL_HTTP

    @property
    def stop(self):
        return self.STOP

    @stop.setter
    def stop(self, STOP):
        if not isinstance(STOP, str):
            raise ValueError("stop must be an string!")
        if len(STOP) == 0:
            raise ValueError("stop can not be empty!")
        self.STOP = STOP


    def parse_response(self, res, audio_data):
        print("--------------------------- response ---------------------------")
        # print(f"response raw bytes: {res}")
        protocol_version = res[0] >> 4
        header_size = res[0] & 0x0f
        message_type = res[1] >> 4
        message_type_specific_flags = res[1] & 0x0f
        serialization_method = res[2] >> 4
        message_compression = res[2] & 0x0f
        reserved = res[3]
        header_extensions = res[4:header_size * 4]
        payload = res[header_size * 4:]
        print(f"            Protocol version: {protocol_version:#x} - version {protocol_version}")
        print(f"                 Header size: {header_size:#x} - {header_size * 4} bytes ")
        print(f"                Message type: {message_type:#x} - {MESSAGE_TYPES[message_type]}")
        print(
            f" Message type specific flags: {message_type_specific_flags:#x} - {MESSAGE_TYPE_SPECIFIC_FLAGS[message_type_specific_flags]}")
        print(
            f"Message serialization method: {serialization_method:#x} - {MESSAGE_SERIALIZATION_METHODS[serialization_method]}")
        print(f"         Message compression: {message_compression:#x} - {MESSAGE_COMPRESSIONS[message_compression]}")
        print(f"                    Reserved: {reserved:#04x}")
        if header_size != 1:
            print(f"           Header extensions: {header_extensions}")
        if message_type == 0xb:  # audio-only server response
            if message_type_specific_flags == 0:  # no sequence number as ACK
                print("                Payload size: 0")
                return False
            else:
                sequence_number = int.from_bytes(payload[:4], "big", signed=True)
                payload_size = int.from_bytes(payload[4:8], "big", signed=False)
                payload = payload[8:]
                print(f"             Sequence number: {sequence_number}")
                print(f"                Payload size: {payload_size} bytes")
            audio_data.write(payload)
            if sequence_number < 0:
                return True
            else:
                return False
        elif message_type == 0xf:
            code = int.from_bytes(payload[:4], "big", signed=False)
            msg_size = int.from_bytes(payload[4:8], "big", signed=False)
            error_msg = payload[8:]
            if message_compression == 1:
                error_msg = gzip.decompress(error_msg)
            error_msg = str(error_msg, "utf-8")
            print(f"          Error message code: {code}")
            print(f"          Error message size: {msg_size} bytes")
            print(f"               Error message: {error_msg}")
            return True
        elif message_type == 0xc:
            msg_size = int.from_bytes(payload[:4], "big", signed=False)
            payload = payload[4:]
            if message_compression == 1:
                payload = gzip.decompress(payload)
            print(f"            Frontend message: {payload}")
        else:
            print("undefined message type!")
            return True

    def parse_response_stream(self, res):
        print("--------------------------- response ---------------------------")
        # print(f"response raw bytes: {res}")
        protocol_version = res[0] >> 4
        header_size = res[0] & 0x0f
        message_type = res[1] >> 4
        message_type_specific_flags = res[1] & 0x0f
        serialization_method = res[2] >> 4
        message_compression = res[2] & 0x0f
        reserved = res[3]
        header_extensions = res[4:header_size * 4]
        payload = res[header_size * 4:]

        print(f"            Protocol version: {protocol_version:#x} - version {protocol_version}")
        print(f"                 Header size: {header_size:#x} - {header_size * 4} bytes ")
        print(f"                Message type: {message_type:#x} - {MESSAGE_TYPES[message_type]}")
        print(
            f" Message type specific flags: {message_type_specific_flags:#x} - {MESSAGE_TYPE_SPECIFIC_FLAGS[message_type_specific_flags]}")
        print(
            f"Message serialization method: {serialization_method:#x} - {MESSAGE_SERIALIZATION_METHODS[serialization_method]}")
        print(f"         Message compression: {message_compression:#x} - {MESSAGE_COMPRESSIONS[message_compression]}")
        print(f"                    Reserved: {reserved:#04x}")

        if header_size != 1:
            print(f"           Header extensions: {header_extensions}")

        # 处理音频数据的消息类型
        if message_type == 0xb:  # audio-only server response
            if message_type_specific_flags == 0:  # no sequence number as ACK
                print("                Payload size: 0")
                return None, False  # 没有有效数据时返回 None 和 False
            else:
                sequence_number = int.from_bytes(payload[:4], "big", signed=True)
                payload_size = int.from_bytes(payload[4:8], "big", signed=False)
                payload = payload[8:]
                print(f"             Sequence number: {sequence_number}")
                print(f"                Payload size: {payload_size} bytes")

                # 返回音频数据块
                if sequence_number < 0:
                    return payload, True  # 返回数据块，并指示结束
                else:
                    return payload, False  # 返回数据块，未结束

        # 错误消息类型
        elif message_type == 0xf:
            code = int.from_bytes(payload[:4], "big", signed=False)
            msg_size = int.from_bytes(payload[4:8], "big", signed=False)
            error_msg = payload[8:]
            if message_compression == 1:
                error_msg = gzip.decompress(error_msg)
            error_msg = str(error_msg, "utf-8")
            print(f"          Error message code: {code}")
            print(f"          Error message size: {msg_size} bytes")
            print(f"               Error message: {error_msg}")
            return None, True  # 返回 None，指示结束

        # 前端消息类型
        elif message_type == 0xc:
            msg_size = int.from_bytes(payload[:4], "big", signed=False)
            payload = payload[4:]
            if message_compression == 1:
                payload = gzip.decompress(payload)
            print(f"            Frontend message: {payload}")
            return None, False  # 没有音频数据，未结束

        # 未定义的消息类型
        else:
            print("undefined message type!")
            return None, True  # 未定义类型，指示结束

    def _get_voice_type(self, character_category):
        # voice_type = "BV700_streaming"
        # if character_category == -2:
        #     voice_type = "BV426_streaming"
        # elif character_category == -1:
        #     voice_type = "BV007_streaming"
        # elif character_category == 0 or character_category == 1:
        #     voice_type = "BV051_streaming"
        # elif character_category == 2:
        #     voice_type = "BV700_streaming"
        # elif character_category == 3:
        #     voice_type = "BV102_streaming"
        # elif character_category == 4:
        #     voice_type = "BV700_streaming"
        # elif character_category == 5:
        #     voice_type = "BV102_streaming"
        # elif character_category == 6 or character_category == 7:
        #     voice_type = "BV007_streaming"
        # return voice_type
        return "BV700_streaming"

    async def tts_ws(self, phrase, silent=125, speed_ratio=1.0, emotion="happy", character_category=-1, operation="query"):
        submit_request_json = copy.deepcopy(request_json)
        submit_request_json["app"]["appid"] = self.APPID
        submit_request_json["app"]["token"] = self.TOKEN
        submit_request_json["app"]["cluster"] = self.CLUSTER
        submit_request_json["audio"]["voice_type"] = self._get_voice_type(character_category)
        submit_request_json["audio"]["speed_ratio"] = speed_ratio
        if emotion:
            submit_request_json["audio"]["emotion"] = emotion
        submit_request_json["request"]["reqid"] = str(uuid.uuid4())
        submit_request_json["request"]["text"] = phrase
        submit_request_json["request"]["operation"] = operation
        submit_request_json["request"]["silence_duration"] = silent

        # 构建请求并压缩
        payload_bytes = str.encode(json.dumps(submit_request_json))
        payload_bytes = gzip.compress(payload_bytes)

        # 准备发送请求
        full_client_request = bytearray(default_header)
        full_client_request.extend((len(payload_bytes)).to_bytes(4, 'big'))  # payload size(4 bytes)
        full_client_request.extend(payload_bytes)  # payload


        # WebSocket 连接和数据接收
        header = {"Authorization": f"Bearer; {self.TOKEN}"}
        audio_data = BytesIO()  # 用于存储音频数据

        self.STOP = False

        start = time.time()
        async with websockets.connect(self.API_URL_WS, extra_headers=header, ping_interval=None) as ws:
            await ws.send(full_client_request)
            while True:
                res = await ws.recv()
                done = self.parse_response(res, audio_data)
                if done:
                    break
                if self.STOP:
                    break
        print('tts_stream Time elapsed:', round(time.time() - start, 3))
        # 返回音频数据
        return audio_data

    async def tts_ws_stream(self, phrase, silent=125, speed_ratio=1.0, emotion="happy", voice_type="BV700_streaming", operation="submit"):
        submit_request_json = copy.deepcopy(request_json)
        submit_request_json["app"]["appid"] = self.APPID
        submit_request_json["app"]["token"] = self.TOKEN
        submit_request_json["app"]["cluster"] = self.CLUSTER
        submit_request_json["audio"]["voice_type"] = voice_type
        submit_request_json["audio"]["rate"] = 24000
        submit_request_json["audio"]["speed_ratio"] = speed_ratio
        if emotion:
            submit_request_json["audio"]["emotion"] = emotion
        submit_request_json["audio"]["encoding"] = "pcm"
        submit_request_json["request"]["reqid"] = str(uuid.uuid4())
        submit_request_json["request"]["text"] = phrase
        submit_request_json["request"]["operation"] = operation
        submit_request_json["request"]["silence_duration"] = silent

        print(f'submit_request_json------------------------{submit_request_json}')

        # 构建请求并压缩
        payload_bytes = str.encode(json.dumps(submit_request_json))
        payload_bytes = gzip.compress(payload_bytes)

        # 准备发送请求
        full_client_request = bytearray(default_header)
        full_client_request.extend((len(payload_bytes)).to_bytes(4, 'big'))  # payload size(4 bytes)
        full_client_request.extend(payload_bytes)  # payload

        # WebSocket 连接和数据接收
        header = {"Authorization": f"Bearer; {self.TOKEN}"}

        self.STOP = False

        start = time.time()
        async with websockets.connect(self.API_URL_WS, extra_headers=header, ping_interval=None) as ws:
            await ws.send(full_client_request)
            while not self.STOP:
                res = await ws.recv()
                audio_chunk, done = self.parse_response_stream(res)

                if audio_chunk:  # 如果有音频数据块
                    yield audio_chunk  # 返回音频数据块

                if done:
                    break

    def TTS(self, phrase, silent=125, speed_ratio=1.0, emotion="happy", character_category=-1):
        header = {"Authorization": f"Bearer; {self.TOKEN}"}

        submit_request_json = copy.deepcopy(request_json)
        submit_request_json["app"]["appid"] = self.APPID
        submit_request_json["app"]["token"] = self.TOKEN
        submit_request_json["app"]["cluster"] = self.CLUSTER
        submit_request_json["audio"]["voice_type"] = self._get_voice_type(character_category)
        submit_request_json["audio"]["speed_ratio"] = speed_ratio
        if emotion:
            submit_request_json["audio"]["emotion"] = emotion
        submit_request_json["request"]["reqid"] = str(uuid.uuid4())
        submit_request_json["request"]["text"] = phrase
        submit_request_json["request"]["operation"] = "query"
        submit_request_json["request"]["silence_duration"] = silent

        start = time.time()
        resp = requests.post(self.API_URL_HTTP, json.dumps(submit_request_json), headers=header)
        if "data" in resp.json():
            data = resp.json()["data"]
            # file_to_save = open("test_submit.mp3", "wb")
            # file_to_save.write(base64.b64decode(data))
            print('tts Time elapsed:', round(time.time() - start, 3))
            return data

    def stop_websocket_stream(self):
        self.STOP = True


    def execute_one(self, audio_item, cluster, **kwargs):
        """

        :param audio_item: {"id": xxx, "path": "xxx"}
        :param cluster:集群名称
        :return:
        """
        assert 'id' in audio_item
        assert 'path' in audio_item
        audio_id = audio_item['id']
        audio_path = audio_item['path']
        audio_type = AudioType.LOCAL
        asr_http_client = AsrWsClient(
            audio_path=audio_path,
            cluster=cluster,
            audio_type=audio_type,
            **kwargs
        )
        result = asyncio.run(asr_http_client.execute())
        return {"id": audio_id, "path": audio_path, "result": result}

    def ASR(self, audio_path, audio_format):
        result = self.execute_one(
            {
                'id': 1,
                'path': audio_path
            },
            cluster=self.ASR_CLUSTER,
            appid=self.APPID,
            token=self.TOKEN,
            format=audio_format,
        )
        return result
