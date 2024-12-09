#!/usr/bin/env python3
import logging

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service

from ble import (
    Advertisement,
    Characteristic,
    Service,
    Application,
    find_adapter,
    Descriptor,
    Agent,
    GATT_CHRC_IFACE
)

import struct
import array
from enum import Enum

import json
import subprocess
import random
import string
import platform
import psutil

def get_mac_address(interface_name):
    # 获取所有网卡接口的信息
    interfaces = psutil.net_if_addrs()

    # 检查是否有指定的接口名
    if interface_name in interfaces:
        # 遍历接口的地址信息
        for address in interfaces[interface_name]:
            if address.family == psutil.AF_LINK:
                return address.address
    return None

MainLoop = None
try:
    from gi.repository import GLib

    MainLoop = GLib.MainLoop
except ImportError:
    import gobject as GObject

    MainLoop = GObject.MainLoop

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logHandler = logging.StreamHandler()
filelogHandler = logging.FileHandler("logs.log")
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logHandler.setFormatter(formatter)
filelogHandler.setFormatter(formatter)
logger.addHandler(filelogHandler)
logger.addHandler(logHandler)

mainloop = None

# 标志用于跟踪广告是否已注册
advertisement_registered = False

BLUEZ_SERVICE_NAME = "org.bluez"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"

class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class NotSupportedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotSupported"


class NotPermittedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotPermitted"


class InvalidValueLengthException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.InvalidValueLength"


class FailedException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.Failed"


def register_app_cb():
    logger.info("GATT application registered")


def register_app_error_cb(error):
    logger.critical("Failed to register application: " + str(error))
    mainloop.quit()


class WifiService(Service):
    """
    Dummy test service that provides characteristics and descriptors that
    exercise various API functionality.

    """

    WIFI_SVC_UUID = "00001ff9-0000-1000-8000-00805f9b34fb"

    def __init__(self, bus, index):
        Service.__init__(self, bus, index, self.WIFI_SVC_UUID, True)
        self.add_characteristic(WifiConfigCharacteristic(bus, 0, self))
        # self.add_characteristic(LLMConfigCharacteristic(bus, 1, self))
        # self.add_characteristic(BoilerControlCharacteristic(bus, 1, self))
        # self.add_characteristic(AutoOffCharacteristic(bus, 2, self))

class LLMService(Service):
    """
    Dummy test service that provides characteristics and descriptors that
    exercise various API functionality.

    """

    LLM_SVC_UUID = "00001fe9-0000-1000-8000-00805f9b34fb"

    def __init__(self, bus, index, on_llm_config_update):
        Service.__init__(self, bus, index, self.LLM_SVC_UUID, True)
        self.add_characteristic(LLMConfigCharacteristic(bus, 0, self, on_llm_config_update))
        # self.add_characteristic(BoilerControlCharacteristic(bus, 1, self))
        # self.add_characteristic(AutoOffCharacteristic(bus, 2, self))

# 自定义异常类（可选）
class PingFailedException(Exception):
    pass

class WifiConfigCharacteristic(Characteristic):
    uuid = "00001ffa-0000-1000-8000-00805f9b34fb"
    description = b"Config wifi {'SSID', 'PASSWORD'}"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read", "write", "notify"], service,
        )

        self.value = [0xFF]
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

        # 初始化缓冲区
        self.buffer = bytearray()
        self.buffer_limit = 252 # ssid:32, password:20, 1 unicode = 4 bytes

        self.expected_length = None

        # 超时相关
        self.timeout_id = None
        self.timeout_duration = 5  # 秒

        wired_interface_name = "eth0"
        self.eth_mac_address = get_mac_address(wired_interface_name)

        self.wifi_config_status = 0  # 初值：0， 成功：1， 蓝牙配网异常：-1，WLAN连接失败：-2，WLAN连接成功，网络ping不通：-3，

    def ReadValue(self, options):
        logger.debug("power Read: " + repr(self.value))
        # res = None
        # try:
        #     res = requests.get(VivaldiBaseUrl + "/vivaldi")
        #     self.value = bytearray(res.json()["machine"], encoding="utf8")
        # except Exception as e:
        #     logger.error(f"Error getting status {e}")
        #     self.value = bytearray(self.State.unknown, encoding="utf8")

        return self.value

    def WriteValue(self, value, options):
        logger.debug("Wificonfig Write: " + repr(value))

        self.notify_info("正在发送配置...")

        self.buffer += bytes(value)
        logger.debug(f"当前缓冲区内容: {self.buffer}")

        logger.info(f"len(self.buffer): {len(self.buffer)} 字节")

        logger.info(f"self.buffer_limit: {self.buffer_limit} 字节")

        logger.info(f"预期接收长度: {self.expected_length} 字节")

        # 检查缓冲区是否超过最大长度
        if len(self.buffer) > self.buffer_limit:
            logger.error("缓冲区长度超过限制，清空缓冲区")
            self.buffer = bytearray()
            if self.timeout_id:
                GLib.source_remove(self.timeout_id)
                self.timeout_id = None
            status = -4
            self.notify_status(status)
            return

        # 重置超时
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
        self.timeout_id = GLib.timeout_add_seconds(self.timeout_duration, self.on_timeout)

        logger.info(f"预期接收长度: {self.expected_length} 字节")

        # 处理长度前缀
        if self.expected_length is None:
            if len(self.buffer) >= 4:
                self.expected_length = struct.unpack('>I', self.buffer[:4])[0]
                self.buffer = self.buffer[4:]
                logger.info(f"预期接收长度: {self.expected_length} 字节")
            else:
                return  # 尚未接收完长度前缀

        # 检查是否接收完整的数据
        if len(self.buffer) >= self.expected_length:
            json_bytes = self.buffer[:self.expected_length]
            self.buffer = self.buffer[self.expected_length:]
            self.expected_length = None  # 重置预期长度

            try:
                wifi_config = json_bytes.decode("utf-8")
                logger.info(f"完整的WiFi配置: {wifi_config}")

                # 解析JSON
                config_data = json.loads(wifi_config)
                ssid = config_data.get("ssid")
                password = config_data.get("password")
                logger.info(f"SSID: {ssid}, Password: {password}")

                self.notify_info("配置发送成功，开始配置设备网络...")

                # 配置WiFi
                self.configure_wifi(ssid, password)
                print(f"Configured WiFi with SSID: {ssid} and Password: {password}")

                # 发送通知
                self.notify_status(self.wifi_config_status)

                # if mainloop:
                #     GLib.idle_add(stop_mainloop)
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析错误: {e}")
                # 触发通知
                status = -1
                self.notify_status(status)
            except subprocess.CalledProcessError as e:
                # 捕获上层代码在执行nmcli配置wifi时的异常，根据上层代码设置的wifi_config_status触发通知
                self.notify_status(self.wifi_config_status)
            except PingFailedException as e:
                logger.error(f"接入WiFi后无法ping通网络: {e}")
                # 捕获上层代码在执行nmcli配置wifi时的异常，根据上层代码设置的wifi_config_status触发通知
                self.notify_status(self.wifi_config_status)
            except Exception as e:
                logger.error(f"处理WiFi配置时发生错误: {e}")
                # 触发通知
                status = -1
                self.notify_status(status)

            # 清空缓冲区中多余的数据
            if len(self.buffer) > 0:
                logger.debug("清空缓冲区中多余的数据")
                self.buffer = bytearray()

            # 取消超时
            if self.timeout_id:
                GLib.source_remove(self.timeout_id)
                self.timeout_id = None

    def on_timeout(self):
        """超时回调函数，清空缓冲区并记录日志"""
        if self.buffer:
            logger.warning("接收数据超时，清空缓冲区")
            self.buffer = bytearray()
        self.timeout_id = None
        # 发送超时通知
        status = -1
        self.notify_status(status)
        return False  # 不重复调用

    def ping(self, host='8.8.8.8', count=1):
        """
        Ping a host to check network connectivity.
        """
        param = '-n' if platform.system().lower() == 'windows' else '-c'
        command = ['ping', param, str(count), host]
        try:
            output = subprocess.check_output(command, stderr=subprocess.STDOUT, universal_newlines=True)
            logger.info(f"Ping 成功:\n{output}")
            return True
        except subprocess.CalledProcessError as e:
            logger.info(f"Ping 失败:\n{e.output}")
            return False

    # 配置WiFi的功能
    def configure_wifi(self, ssid, password):
        command = ["nmcli", "d", "wifi", "connect", ssid, "password", password]
        try:
            subprocess.run(command, check=True)
            self.notify_info("设备WiFi接入成功，正在测试网络...")
            if self.ping(count=3):
                self.wifi_config_status = 1
                logger.info("WiFi配置并连接成功")
            else:
                logger.error("WiFi配置成功，但无法ping通目标，连接可能不稳定")
                raise PingFailedException("Ping目标失败，WiFi连接可能存在问题")
        except subprocess.CalledProcessError as e:
            self.wifi_config_status = -2
            logger.error(f"配置WiFi失败: {e}")
            raise  # 重新抛出异常以便上层处理
        except PingFailedException as e:
            # 如果需要，你可以在这里处理Ping失败的异常
            self.wifi_config_status = -3
            logger.error(f"异常捕获: {e}")
            raise  # 重新抛出异常以便上层处理
        except Exception as e:
            self.wifi_config_status = -2
            logger.error(f"未知错误: {e}")
            raise  # 重新抛出异常以便上层处理

    # status = 0  # 初值：0， 成功：1， 蓝牙配网异常：-1，WLAN连接失败：-2，WLAN连接成功，网络ping不通：-3，接入点和密码超出52个字符：-4
    def notify_status(self, status):
        """向所有已订阅的客户端发送通知，type：1，信息；2，状态"""
        if self.notifying:
            notification_str = json.dumps({"type": 2, "mac": self.eth_mac_address, "status": status})
            self.sendNotification(notification_str)

    def notify_info(self, info):
        """向所有已订阅的客户端发送通知，type：1，信息；2，状态"""
        if self.notifying:
            notification_str = json.dumps({"type": 1, "info": info})
            self.sendNotification(notification_str)

    def sendNotification(self, notification_str):
        notification_bytes = bytearray(notification_str, encoding='utf-8')
        logger.info(f"发送通知: {notification_str}")

        # 分割消息，按 20 字节一包
        mtu_size = 20  # BLE 单个通知的最大数据长度
        total_length = len(notification_bytes)
        num_packets = (total_length + mtu_size - 1) // mtu_size  # 计算包的总数

        for i in range(num_packets):
            # 取出分包的片段
            start = i * mtu_size
            end = start + mtu_size
            packet = notification_bytes[start:end]

            # 更新特征值
            self.value = [dbus.Byte(b) for b in packet]

            # 记录日志，便于调试
            logger.info(f"发送第 {i + 1}/{num_packets} 个分包: {packet}")

            # 发送 PropertiesChanged 信号以触发通知
            self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": dbus.Array(self.value, signature='y')}, [])
        # notification_bytes = bytearray(notification_str, encoding='utf-8')
        # logger.info(f"发送通知: {notification_str}")
        # # 更新特征的值
        # self.value = [dbus.Byte(b) for b in notification_bytes]
        # # 发送 PropertiesChanged 信号以触发通知
        # self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": dbus.Array(self.value, signature='y')}, [])

class LLMConfigCharacteristic(Characteristic):
    uuid = "00001fea-0000-1000-8000-00805f9b34fb"
    description = b"Config llm {'prompt', 'voiceType'}"

    def __init__(self, bus, index, service, on_llm_config_update):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["read", "write", "notify"], service,
        )

        self.value = [0xFF]
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

        # 初始化缓冲区
        self.buffer = bytearray()
        self.buffer_limit = 1024 # ssid:1004, password:20, 1 unicode = 4 bytes

        self.expected_length = None

        # 超时相关
        self.timeout_id = None
        self.timeout_duration = 5  # 秒

        wired_interface_name = "eth0"
        self.eth_mac_address = get_mac_address(wired_interface_name)

        self.llm_config_status = 0

        self.on_llm_config_update = on_llm_config_update

    def ReadValue(self, options):
        logger.debug("power Read: " + repr(self.value))
        # res = None
        # try:
        #     res = requests.get(VivaldiBaseUrl + "/vivaldi")
        #     self.value = bytearray(res.json()["machine"], encoding="utf8")
        # except Exception as e:
        #     logger.error(f"Error getting status {e}")
        #     self.value = bytearray(self.State.unknown, encoding="utf8")

        return self.value

    def WriteValue(self, value, options):
        logger.debug("LLM config Write: " + repr(value))

        self.notify_info("正在发送配置...")

        self.buffer += bytes(value)
        logger.debug(f"当前缓冲区内容: {self.buffer}")

        logger.info(f"len(self.buffer): {len(self.buffer)} 字节")

        logger.info(f"self.buffer_limit: {self.buffer_limit} 字节")

        logger.info(f"预期接收长度: {self.expected_length} 字节")

        # 检查缓冲区是否超过最大长度
        if len(self.buffer) > self.buffer_limit:
            logger.error("缓冲区长度超过限制，清空缓冲区")
            self.buffer = bytearray()
            if self.timeout_id:
                GLib.source_remove(self.timeout_id)
                self.timeout_id = None
            status = -4
            self.notify_status(status)
            return

        # 重置超时
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
        self.timeout_id = GLib.timeout_add_seconds(self.timeout_duration, self.on_timeout)

        logger.info(f"预期接收长度: {self.expected_length} 字节")

        # 处理长度前缀
        if self.expected_length is None:
            if len(self.buffer) >= 4:
                self.expected_length = struct.unpack('>I', self.buffer[:4])[0]
                self.buffer = self.buffer[4:]
                logger.info(f"预期接收长度: {self.expected_length} 字节")
            else:
                return  # 尚未接收完长度前缀

        # 检查是否接收完整的数据
        if len(self.buffer) >= self.expected_length:
            json_bytes = self.buffer[:self.expected_length]
            self.buffer = self.buffer[self.expected_length:]
            self.expected_length = None  # 重置预期长度

            try:
                llm_config = json_bytes.decode("utf-8")
                logger.info(f"完整的LLM配置: {llm_config}")

                # 解析JSON
                config_data = json.loads(llm_config)
                prompt = config_data.get("prompt")
                voice_type = config_data.get("voiceType")
                logger.info(f"prompt: {prompt}, voiceType: {voice_type}")

                self.on_llm_config_update(config_data)

                self.notify_info("模型配置发送成功")

                self.llm_config_status = 1

                # 发送通知
                self.notify_status(self.llm_config_status)
                #
                # if mainloop:
                #     GLib.idle_add(stop_mainloop)
            except json.JSONDecodeError as e:
                logger.error(f"JSON解析错误: {e}")
                # 触发通知
                status = -1
                self.notify_status(status)
            except Exception as e:
                logger.error(f"处理LLM配置时发生错误: {e}")
                # 触发通知
                status = -1
                self.notify_status(status)

            # 清空缓冲区中多余的数据
            if len(self.buffer) > 0:
                logger.debug("清空缓冲区中多余的数据")
                self.buffer = bytearray()

            # 取消超时
            if self.timeout_id:
                GLib.source_remove(self.timeout_id)
                self.timeout_id = None

    def on_timeout(self):
        """超时回调函数，清空缓冲区并记录日志"""
        if self.buffer:
            logger.warning("接收数据超时，清空缓冲区")
            self.buffer = bytearray()
        self.timeout_id = None
        # 发送超时通知
        status = -1
        self.notify_status(status)
        return False  # 不重复调用

    # status = 0  # 初值：0， 成功：1， 蓝牙配网异常：-1，WLAN连接失败：-2，WLAN连接成功，网络ping不通：-3，接入点和密码超出52个字符：-4
    def notify_status(self, status):
        """向所有已订阅的客户端发送通知，type：1，信息；2，状态"""
        if self.notifying:
            notification_str = json.dumps({"type": 2, "mac": self.eth_mac_address, "status": status})
            self.sendNotification(notification_str)

    def notify_info(self, info):
        """向所有已订阅的客户端发送通知，type：1，信息；2，状态"""
        if self.notifying:
            notification_str = json.dumps({"type": 1, "info": info})
            self.sendNotification(notification_str)

    def sendNotification(self, notification_str):
        notification_bytes = bytearray(notification_str, encoding='utf-8')
        logger.info(f"发送通知: {notification_str}")

        # 分割消息，按 20 字节一包
        mtu_size = 20  # BLE 单个通知的最大数据长度
        total_length = len(notification_bytes)
        num_packets = (total_length + mtu_size - 1) // mtu_size  # 计算包的总数

        for i in range(num_packets):
            # 取出分包的片段
            start = i * mtu_size
            end = start + mtu_size
            packet = notification_bytes[start:end]

            # 更新特征值
            self.value = [dbus.Byte(b) for b in packet]

            # 记录日志，便于调试
            logger.info(f"发送第 {i + 1}/{num_packets} 个分包: {packet}")

            # 发送 PropertiesChanged 信号以触发通知
            self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": dbus.Array(self.value, signature='y')}, [])

class PowerControlCharacteristic(Characteristic):
    uuid = "4116f8d2-9f66-4f58-a53d-fc7440e7c14e"
    description = b"Get/set machine power state {'ON', 'OFF', 'UNKNOWN'}"

    class State(Enum):
        on = "ON"
        off = "OFF"
        unknown = "UNKNOWN"

        @classmethod
        def has_value(cls, value):
            return value in cls._value2member_map_

    power_options = {"ON", "OFF", "UNKNOWN"}

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["encrypt-read", "encrypt-write"], service,
        )

        self.value = [0xFF]
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.debug("power Read: " + repr(self.value))
        # res = None
        # try:
        #     res = requests.get(VivaldiBaseUrl + "/vivaldi")
        #     self.value = bytearray(res.json()["machine"], encoding="utf8")
        # except Exception as e:
        #     logger.error(f"Error getting status {e}")
        #     self.value = bytearray(self.State.unknown, encoding="utf8")

        return self.value

    def WriteValue(self, value, options):
        logger.debug("power Write: " + repr(value))
        cmd = bytes(value).decode("utf-8")
        # if self.State.has_value(cmd):
        #     # write it to machine
        #     logger.info("writing {cmd} to machine")
        #     data = {"cmd": cmd.lower()}
        #     try:
        #         res = requests.post(VivaldiBaseUrl + "/vivaldi/cmds", json=data)
        #     except Exceptions as e:
        #         logger.error(f"Error updating machine state: {e}")
        # else:
        #     logger.info(f"invalid state written {cmd}")
        #     raise NotPermittedException

        self.value = value


class BoilerControlCharacteristic(Characteristic):
    uuid = "322e774f-c909-49c4-bd7b-48a4003a967f"
    description = b"Get/set boiler power state can be `on` or `off`"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["encrypt-read", "encrypt-write"], service,
        )

        self.value = []
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.info("boiler read: " + repr(self.value))
        # res = None
        # try:
        #     res = requests.get(VivaldiBaseUrl + "/vivaldi")
        #     self.value = bytearray(res.json()["boiler"], encoding="utf8")
        # except Exception as e:
        #     logger.error(f"Error getting status {e}")

        return self.value

    def WriteValue(self, value, options):
        logger.info("boiler state Write: " + repr(value))
        cmd = bytes(value).decode("utf-8")

        # # write it to machine
        # logger.info("writing {cmd} to machine")
        # data = {"cmd": "setboiler", "state": cmd.lower()}
        # try:
        #     res = requests.post(VivaldiBaseUrl + "/vivaldi/cmds", json=data)
        #     logger.info(res)
        # except Exceptions as e:
        #     logger.error(f"Error updating machine state: {e}")
        #     raise


class AutoOffCharacteristic(Characteristic):
    uuid = "9c7dbce8-de5f-4168-89dd-74f04f4e5842"
    description = b"Get/set autoff time in minutes"

    def __init__(self, bus, index, service):
        Characteristic.__init__(
            self, bus, index, self.uuid, ["secure-read", "secure-write"], service,
        )

        self.value = []
        self.add_descriptor(CharacteristicUserDescriptionDescriptor(bus, 1, self))

    def ReadValue(self, options):
        logger.info("auto off read: " + repr(self.value))
        # res = None
        # try:
        #     res = requests.get(VivaldiBaseUrl + "/vivaldi")
        #     self.value = bytearray(struct.pack("i", int(res.json()["autoOffMinutes"])))
        # except Exception as e:
        #     logger.error(f"Error getting status {e}")

        return self.value

    def WriteValue(self, value, options):
        logger.info("auto off write: " + repr(value))
        cmd = bytes(value)

        # # write it to machine
        # logger.info("writing {cmd} to machine")
        # data = {"cmd": "autoOffMinutes", "time": struct.unpack("i", cmd)[0]}
        # try:
        #     res = requests.post(VivaldiBaseUrl + "/vivaldi/cmds", json=data)
        #     logger.info(res)
        # except Exceptions as e:
        #     logger.error(f"Error updating machine state: {e}")
        #     raise


class CharacteristicUserDescriptionDescriptor(Descriptor):
    """
    Writable CUD descriptor.
    """

    CUD_UUID = "2901"

    def __init__(
        self, bus, index, characteristic,
    ):

        self.value = array.array("B", characteristic.description)
        self.value = self.value.tolist()
        Descriptor.__init__(self, bus, index, self.CUD_UUID, ["read"], characteristic)

    def ReadValue(self, options):
        return self.value

    def WriteValue(self, value, options):
        if not self.writable:
            raise NotPermittedException()
        self.value = value


class WifiAdvertisement(Advertisement):
    def __init__(self, bus, index):
        Advertisement.__init__(self, bus, index, "peripheral")
        self.add_manufacturer_data(
            0xFFFF, [0x70, 0x74],
        )
        self.add_service_uuid(WifiService.WIFI_SVC_UUID)
        self.add_service_uuid(LLMService.LLM_SVC_UUID)

        characters = string.ascii_letters + string.digits  # 包含字母和数字
        random_string = ''.join(random.choice(characters) for _ in range(4))

        self.add_local_name(f"布丁智能体盒子_{random_string}")
        self.include_tx_power = True

def register_ad_cb():
    logger.info("Advertisement registered")


def register_ad_error_cb(error):
    logger.critical("Failed to register advertisement: " + str(error))
    mainloop.quit()

AGENT_PATH = "/com/microreal/agent"


def ble_main(on_llm_config_update):
    global mainloop, advertisement_registered

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    # get the system bus
    bus = dbus.SystemBus()
    # get the ble controller
    adapter = find_adapter(bus)

    if not adapter:
        logger.critical("GattManager1 interface not found")
        return

    adapter_obj = bus.get_object(BLUEZ_SERVICE_NAME, adapter)
    adapter_props = dbus.Interface(adapter_obj, "org.freedesktop.DBus.Properties")
    # powered property on the controller to on
    adapter_props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(1))

    adapter_props.Set("org.bluez.Adapter1", "Discoverable", dbus.Boolean(1))
    adapter_props.Set("org.bluez.Adapter1", "Pairable", dbus.Boolean(1))
    adapter_props.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(0))  # 永久可配对
    # Get manager objs
    service_manager = dbus.Interface(adapter_obj, GATT_MANAGER_IFACE)
    ad_manager = dbus.Interface(adapter_obj, LE_ADVERTISING_MANAGER_IFACE)
    advertisement = WifiAdvertisement(bus, 0)
    obj = bus.get_object(BLUEZ_SERVICE_NAME, "/org/bluez")
    agent = Agent(bus, AGENT_PATH)

    app = Application(bus)
    app.add_service(WifiService(bus, 2))
    app.add_service(LLMService(bus, 3, on_llm_config_update))
    mainloop = MainLoop()

    agent_manager = dbus.Interface(obj, "org.bluez.AgentManager1")
    agent_manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
    try:
        ad_manager.RegisterAdvertisement(
            advertisement.get_path(),
            {},
            reply_handler=register_ad_cb,
            error_handler=register_ad_error_cb,
        )
        advertisement_registered = True

        logger.info("Registering GATT application...")

        service_manager.RegisterApplication(
            app.get_path(),
            {},
            reply_handler=register_app_cb,
            error_handler=[register_app_error_cb],
        )
        agent_manager.RequestDefaultAgent(AGENT_PATH)
        logger.info("启动BLE服务并进入主循环")
        mainloop.run()
    except KeyboardInterrupt:
        logger.info("主循环已停止")
    finally:
        # 清理工作，例如注销广告
        if 'advertisement_registered' in globals() and advertisement_registered:
            try:
                ad_manager.UnregisterAdvertisement(advertisement)
                advertisement_registered = False
                logger.info("Advertisement unregistered successfully")
            except dbus.exceptions.DBusException as e:
                logger.error(f"Failed to unregister advertisement: {e}")
        try:
            dbus.service.Object.remove_from_connection(advertisement)
        except dbus.exceptions.DBusException as e:
            logger.error(f"Failed to remove advertisement from connection: {e}")
        logger.info("已注销广告并清理资源")