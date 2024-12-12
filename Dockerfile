# 使用官方 Python 3.9-slim 基础镜像
FROM python:3.9-slim

# 设置工作目录，并复制代码
WORKDIR /app
COPY . .

# 安装依赖库
RUN apt-get update && apt-get install -y \
    pulseaudio \
    python3-pyaudio \
    pulseaudio-module-bluetooth \
    build-essential \
    libncurses5-dev \
    libgdbm-dev \
    libnss3-dev \
    libssl-dev \
    libreadline-dev \
    libffi-dev \
    zlib1g-dev \
    libsqlite3-dev \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    libgirepository1.0-dev \
    gir1.2-glib-2.0 \
    build-essential \
    libglib2.0-dev \
    libdbus-1-dev \
    libudev-dev \
    libical-dev \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    libgirepository1.0-dev \
    meson \
    ninja-build \
    portaudio19-dev \
    python3-pip \
    python3-dbus \
    libdbus-1-dev \
    libglib2.0-dev \
    portaudio19-dev \
    python3-pyaudio \
    sox  \
    pulseaudio \
    libsox-fmt-all \
    ffmpeg \
    libatlas-base-dev \
    libtool \
    unzip \
    && rm -rf /var/lib/apt/lists/* \

# 安装 Python 依赖
RUN pip install --upgrade pip setuptools wheel \
    && pip install meson ninja \
    && pip install -r requirements.txt \

# 安装dbus
RUN git clone https://gitlab.freedesktop.org/dbus/dbus-python.git \
    && cd dbus-python \
    && cd dbus-python \
    && python3.9 setup.py install \
    && pip install dbus-python==1.2.16

# 设置正确的时区
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && dpkg-reconfigure -f noninteractive tzdata

# 安装leptonica
RUN wget https://github.com/DanBloomberg/leptonica/releases/download/1.85.0/leptonica-1.85.0.tar.gz \
    && tar -xvf leptonica-1.85.0.tar.gz \
    && cd leptonica-1.85.0 | \
    && ./autogen.sh \
    && ./configure --prefix=/usr/local/leptonica \
    && make \
    && make install \

# 修改 /etc/profile 文件
RUN echo 'PKG_CONFIG_PATH=$PKG_CONFIG_PATH:/usr/local/leptonica/lib/pkgconfig' >> /etc/profile && \
    echo 'export PKG_CONFIG_PATH' >> /etc/profile && \
    echo 'CPLUS_INCLUDE_PATH=$CPLUS_INCLUDE_PATH:/usr/local/leptonica/include/leptonica' >> /etc/profile && \
    echo 'export CPLUS_INCLUDE_PATH' >> /etc/profile && \
    echo 'C_INCLUDE_PATH=$C_INCLUDE_PATH:/usr/local/leptonica/include/leptonica' >> /etc/profile && \
    echo 'export C_INCLUDE_PATH' >> /etc/profile && \
    echo 'LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/leptonica/lib' >> /etc/profile && \
    echo 'export LD_LIBRARY_PATH' >> /etc/profile && \
    echo 'LIBRARY_PATH=$LIBRARY_PATH:/usr/local/leptonica/lib' >> /etc/profile && \
    echo 'export LIBRARY_PATH' >> /etc/profile && \
    echo 'LIBLEPT_HEADERSDIR=/usr/local/leptonica/include/leptonica' >> /etc/profile && \
    echo 'export LIBLEPT_HEADERSDIR' >> /etc/profile

# 确保环境变量在 Docker 构建阶段生效
RUN . /etc/profile

# 安装tesseract 5.4.0
RUN cd tesseract \
    && unzip tesseract-5.4.0.zip \
    && cd tesseract-5.4.0 \
    && ./autogen.sh \
    && ./configure --prefix=/usr/local/tesseract \
    && make \
    && make install \

# 修改 /etc/profile 文件
RUN echo 'PATH=$PATH:/usr/local/tesseract/bin' >> /etc/profile && \
    echo 'export PATH' >> /etc/profile && \
    echo 'export TESSDATA_PREFIX=/usr/local/share/tessdata' >> /etc/profile &&

# 确保环境变量在 Docker 构建阶段生效
RUN . /etc/profile

# 将本地 tessdata 目录复制到容器的 /usr/local/share/tessdata
COPY ./tessdata /usr/local/share/tessdata

# 设置环境变量（用户主目录）
ENV HOME=/root

# 创建 .wukong 目录
RUN mkdir -p $HOME/.wukong

# 复制文件到 .wukong 目录
COPY static/config.yml $HOME/.wukong/
COPY static/hidoubao.table $HOME/.wukong/
COPY static/jarvis_zh_iphone.pmdl $HOME/.wukong/

# 编辑用户主目录下的 .bashrc
RUN echo "pulseaudio --start >> /data/install/pudding-robot-build/pulseaudio.log 2>&1" >> $HOME/.bashrc

# 复制和启用 pudding-robot 定时任务
COPY pudding-robot.service /etc/systemd/system/
COPY pudding-robot.timer /etc/systemd/system/
RUN systemctl daemon-reload && systemctl enable pudding-robot.timer

# 设置动态蓝牙音频配置脚本和规则
COPY dynamic-set-bluetooth-profile.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/dynamic-set-bluetooth-profile.sh
COPY 99-bluetooth-profile.rules /etc/udev/rules.d/
RUN udevadm control --reload

# 复制并编译 swig
COPY ./swig-3.0.10.tar.gz .
RUN tar xvf swig-3.0.10.tar.gz \
    && cd swig-3.0.10 \
    && apt-get -y update \
    && apt-get install -y libpcre3 libpcre3-dev \
    && ./configure --prefix=/usr --without-clisp --without-maximum-compile-warnings \
    && make \
    && make install \
    && install -v -m755 -d /usr/share/doc/swig-3.0.10 \
    && cp -v -R Doc/* /usr/share/doc/swig-3.0.10

# 复制 snowboy
#RUN wget https://wzpan-1253537070.cos.ap-guangzhou.myqcloud.com/misc/snowboy.tar.bz2 \
#    && tar -xvjf snowboy.tar.bz2 \
#    && cd snowboy/swig/Python3 \
#    && make \
#    && cp _snowboydetect.so /app/snowboy/
#COPY ./snowboy/_snowboydetect.so /app/snowboy/
#COPY ./snowboy/resources /app/snowboy/

# 下载并安装geckodriver
#RUN wget https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-linux-aarch64.tar.gz \
#    && tar xvf geckodriver-v0.35.0-linux-aarch64.tar.gz \
#    && mv geckodriver /usr/local/bin/ \
#    && chmod +x /usr/local/bin/geckodriver

# 暴露端口
EXPOSE 5001

# 配置容器的入口进程为 systemd
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]