一.安装必要的库：
1.apt换国内源（阿里/清华等），并执行apt update
2.apt install pulseaudio python3-pyaudio pulseaudio-module-bluetooth build-essential libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev zlib1g-dev libsqlite3-dev libcairo2-dev pkg-config python3-dev libgirepository1.0-dev gir1.2-glib-2.0 build-essential libglib2.0-dev libdbus-1-dev libudev-dev libical-dev libcairo2-dev pkg-config python3-dev libgirepository1.0-dev meson ninja-build portaudio19-dev python3-pip python3-dbus libdbus-1-dev libglib2.0-dev portaudio19-dev python3-pyaudio sox pulseaudio libsox-fmt-all ffmpeg libatlas-base-dev libtool 

二.安装蓝牙驱动：
1.执行armbian-config-Network-BT，安装bluetooth相关软件
2.把meson-g12a-s905l3a-cm311-with-bt(2024.10).dtb复制到/boot/dtb/amlogic/meson-g12a-s905l3a-cm311.dtb
3.把rtl8761b_config_2m复制到/usr/lib/firmware/rtl_bt/rtl8761b_config.bin
4.编辑/etc/systemd/system/bluetooth.service，在[Service]下增加以下内容：
ExecStopPost=/usr/bin/env gpioset 0 82=0
5.编辑或添加~/.asoundrc：
pcm.!default {
    type pulse
}

ctl.!default {
    type pulse
}
6.重置后，执行hciconfig，可以看到蓝牙设备

三.安装python3.9
1.下载python源代码安装包并解压：wget https://www.python.org/ftp/python/3.9.20/Python-3.9.20.tgz
2../configure --enable-optimizations
3.make -j $(nproc)
4.make install

四.创建虚拟环境并激活：
git clone
python3.9 -m venv pudding-venv
source pudding-venv/bin/activate

五.pip安装python相关库：
pip install --upgrade pip setuptools wheel
pip install meson ninja 

六.安装dbus：
1.git clone https://gitlab.freedesktop.org/dbus/dbus-python.git
2.cd dbus-python
3.python3.9  setup.py install
4.pip install dbus-python==1.2.16

七.设置正确的时区：
ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime
dpkg-reconfigure -f noninteractive tzdata

八.安装Tesseract
1.安装leptonica：
wget https://github.com/DanBloomberg/leptonica/releases/download/1.85.0/leptonica-1.85.0.tar.gz
./autogen.sh
./configure --prefix=/usr/local/leptonica
make
make install
2.编辑/etc/profile，增加以下内容：
PKG_CONFIG_PATH=$PKG_CONFIG_PATH:/usr/local/leptonica/lib/pkgconfig
export PKG_CONFIG_PATH
CPLUS_INCLUDE_PATH=$CPLUS_INCLUDE_PATH:/usr/local/leptonica/include/leptonica
export CPLUS_INCLUDE_PATH
C_INCLUDE_PATH=$C_INCLUDE_PATH:/usr/local/leptonica/include/leptonica
export C_INCLUDE_PATH
LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/leptonica/lib
export LD_LIBRARY_PATH
LIBRARY_PATH=$LIBRARY_PATH:/usr/local/leptonica/lib
export LIBRARY_PATH
LIBLEPT_HEADERSDIR=/usr/local/leptonica/include/leptonica
export LIBLEPT_HEADERSDIR

应用设置：source /etc/profile
3.安装tesseract 5.4.0
./autogen.sh
./configure --prefix=/usr/local/tesseract
make
make install
编辑/etc/profile，增加以下内容：
PATH=$PATH:/usr/local/tesseract/bin export PATH export TESSDATA_PREFIX=/usr/local/share/tessdata
4.复制tessdata目录到/usr/local/share/目录下
