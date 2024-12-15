# 支持自定义音色和模型角色的AI音箱

# 👉 自定义音色和模型角色，自由问答：【[用魔百盒改成智能体盒子，接入豆包和GPT，自定义音色和模型角色～](https://www.bilibili.com/video/BV1G1qqYtE78/?vd_source=cb1594a360684634b55fabfd47bac5f2)】

## 👋 基本流程

启动服务后，使用微信小程序搜索智能体盒子，自定义音色和模型角色，即可安装新的音色和角色进行问答。

## ✨ 用到的硬件

- **魔百盒CM311-1a或CM401（主要是搞定蓝牙驱动，其他型号没具体试过）。
- **多多上29.9元的联想蓝牙音箱。
- **如果有wifi接入需求的，再加个usb的无线网卡，大概6-8元。

## ✨ 盒子的OS

armbian 24.10版：[https://github.com/ophub/amlogic-s9xxx-armbian/releases/download/Armbian_bookworm_save_2024.10/Armbian_24.11.0_amlogic_s905l3a_bookworm_6.6.57_server_2024.10.21.img.gz](https://github.com/ophub/amlogic-s9xxx-armbian/releases/download/Armbian_bookworm_save_2024.10/Armbian_24.11.0_amlogic_s905l3a_bookworm_6.6.57_server_2024.10.21.img.gz)，以上是CM311-1a的镜像，CM401a选择相应的版本

## ✨ 软件部分

- **wukong音箱，增加了微软的唤醒词，效果比原来的porcupine和snowboy好了很多；增加了火山引擎的语音处理。
- **MetaGPT，主要是智能体流程，另外修改了多模态的接入，能够处理图片。

## ⚡️ 快速开始
#### Python 3.9

先git clone https://github.com/smile-wingbow/pudding-robot
以下命令都在pudding-robot路径下执行

#### 一.安装必要的库：
1.apt换源
```shell
apt update
```

```shell
apt install pulseaudio python3-pyaudio pulseaudio-module-bluetooth build-essential libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev zlib1g-dev libsqlite3-dev libcairo2-dev pkg-config python3-dev libgirepository1.0-dev gir1.2-glib-2.0 build-essential libglib2.0-dev libdbus-1-dev libudev-dev libical-dev libcairo2-dev pkg-config python3-dev libgirepository1.0-dev meson ninja-build portaudio19-dev python3-pip python3-dbus libdbus-1-dev libglib2.0-dev portaudio19-dev python3-pyaudio sox pulseaudio libsox-fmt-all ffmpeg libatlas-base-dev libtool
```

#### 二.安装蓝牙驱动：
1.进入armbian-config，选择Network，安装蓝牙相关软件
```shell
armbian-config
```
2.把meson-g12a-s905l3a-cm311-with-bt(2024.10).dtb复制到/boot/dtb/amlogic/meson-g12a-s905l3a-cm311.dtb，覆盖前注意最好先备份
```shell
cp bluetooth/meson-g12a-s905l3a-cm311-with-bt(2024.10).dtb /boot/dtb/amlogic/meson-g12a-s905l3a-cm311.dtb
```
3.把rtl8761b_config_2m复制到/usr/lib/firmware/rtl_bt/rtl8761b_config.bin
```shell
cp bluetooth/rtl8761b_config_2m /usr/lib/firmware/rtl_bt/rtl8761b_config.bin
```
4.编辑/etc/systemd/system/bluetooth.service，在[Service]下增加以下内容：
```shell
nano /etc/systemd/system/bluetooth.service
```
```shell
ExecStopPost=/usr/bin/env gpioset 0 82=0
```
5.编辑或添加~/.asoundrc：
```shell
nano ~/.asoundrc
```
6.重启盒子后，执行hciconfig，可以看到蓝牙设备
```shell
hci0:   Type: Primary  Bus: UART
        BD Address: XX:XX:XX:XX:XX:XX  ACL MTU: 1021:5  SCO MTU: 255:11
        UP RUNNING
        RX bytes:4884 acl:0 sco:0 events:212 errors:0
        TX bytes:36121 acl:0 sco:0 commands:212 errors:0
```

#### 三.安装python3.9
1.下载python源代码安装包并解压：
```shell
wget https://www.python.org/ftp/python/3.9.20/Python-3.9.20.tgz
tar -xvf Python-3.9.20.tgz
cd Python-3.9.20
```

```shell
./configure --enable-optimizations
make -j $(nproc)
make install
```

#### 四.创建虚拟环境并激活：
```shell
python3.9 -m venv pudding-venv  
source pudding-venv/bin/activate
```

#### 五.pip安装python相关库：
```shell
pip install --upgrade pip setuptools wheel  
pip install meson ninja  
pip install -r requirements.txt
```

#### 六.安装dbus：
```shell
git clone https://gitlab.freedesktop.org/dbus/dbus-python.git
cd dbus-python
python3.9 setup.py install
pip install dbus-python==1.2.16
```

#### 七.设置正确的时区：
```shell
ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime  
dpkg-reconfigure -f noninteractive tzdata
```

#### 八.在用户主目录下创建.wukong，并复制static目录下的config.yml、hidoubao.table和jarvis_zh_iphone.pmdl
```shell
mkdir ~/.wukong
cp static/config.yml ~/.wukong/
cp static/hidoubao.table ~/.wukong/
cp static/jarvis_zh_iphone.pmdl ~/.wukong/
```

#### 九.如果需要自定义唤醒词，使用微软的自定义关键字定制服务(注意每次仅定义一个，同时定制多个会失败)：https://speech.microsoft.com/portal/7d04ce6f975240ed908f821c0e62eb3c/customkeyword

#### 十.配置接入的各种API key：
1.配置config目录的config2.yaml、doubao_lite_32k.yaml、doubao_lite_4k.yaml、gpt4o.yaml、gpt4omini.yaml配置，上述配置文件是在robot/agents/pudding_agent.py使用，可以自行决定使用哪个模型
2.修改用户主目录下.wukong的config.xml，主要修改TTS和ASR引擎的设置，修改的地方有：指定TTS引擎：tts_engine: volc-tts、指定ASR引擎：asr_engine: volc-asr、以及具体的引擎配置比如（volc_yuyin的具体配置）

#### 十一.修改setting.store，把蓝牙设备的MAC地址改为要连接的音箱的蓝牙MAC地址。
```shell
bluetooth_devices:
- A3:0F:B3:06:1C:A5
```

#### 十二.启动服务：
1.启动pulseaudio
```shell
pulseaudio --start
```
2.使用bluetoothctl来pair、trust和connect音箱
```shell
bluetoothctl pair XX:XX:XX:XX:XX:XX
bluetoothctl trust XX:XX:XX:XX:XX:XX
bluetoothctl connect XX:XX:XX:XX:XX:XX
```
3.使用pactl set-card-profile命令来修改蓝牙连接的profile，启用麦克风(命令中bluez_card.后面的mac地址改为音箱的蓝牙mac地址，注意用"_"替换":")
```shell
pactl set-card-profile  bluez_card.XX_XX_XX_XX_XX_XX handsfree_head_unit
```
4.启动服务
```shell
python3.9 wukong.py
```

## 魔百盒CM401a编译蓝牙驱动

CM311-1a如果刷的是：Armbian_24.11.0_amlogic_s905l3a_bookworm_6.6.57_server_2024.10.21.img.gz  
这个版本，可以直接用github里bluetooth文件夹下编译好的驱动，其他armbian版本可以试试github里的，不行就重新编译：
1.在/boot/dtb/amlogic/下找到对应的dtb文件，CM311-1a是meson-g12a-s905l3a-cm311.dtb，CM401a是meson-g12a-s905l3a-e900v22c.dtb  
2.用dtc反编译dtb文件，得到dts源代码，用法：dtc -I dtb -O dts -o target.dts source.dtb
```shell
dtc -I dtb -O dts -o target.dts source.dtb
```
3.在serial@24000部分增加如下bluetooth内容：
```shell
      bluetooth {  
                compatible = "realtek,rtl8723bs-bt";  
        };
```
4.再把dts源码编译成二进制的dtb，命令：
```shell
dtc -I dts -O dtb -o target.dtb source.dts
```
5.备份原来的dtb文件后，再把编译好的dtb复制回/boot/dtb/amlogic/

## 联系
加群一起讨论

![](https://github.com/smile-wingbow/pudding-robot/blob/main/assets/%E5%BE%AE%E4%BF%A1%E7%BE%A4.jpg?raw=true)

## ❤️ 鸣谢

感谢以下项目提供的贡献：

- https://github.com/ophub/amlogic-s9xxx-armbian
- https://github.com/wzpan/wukong-robot
- https://github.com/geekan/MetaGPT

## 免责声明

本项目仅供学习和研究目的，不得用于任何商业活动。用户在使用本项目时应遵守所在地区的法律法规，对于违法使用所导致的后果，本项目及作者不承担任何责任。 本项目可能存在未知的缺陷和风险（包括但不限于设备损坏和账号封禁等），使用者应自行承担使用本项目所产生的所有风险及责任。 作者不保证本项目的准确性、完整性、及时性、可靠性，也不承担任何因使用本项目而产生的任何损失或损害责任。 使用本项目即表示您已阅读并同意本免责声明的全部内容。

## License

[MIT](https://github.com/idootop/mi-gpt/blob/main/LICENSE) License © 2024-PRESENT  smilewingbow
