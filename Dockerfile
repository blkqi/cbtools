FROM binhex/arch-base:latest

RUN pacman -S --needed --noconfirm python-pip waifu2x-ncnn-vulkan glslang

WORKDIR /app
COPY bin bin
COPY src/cbtools src/cbtools
COPY setup.py setup.py

RUN pip install /app --no-input --break-system-packages

ENV PUID=99
ENV PGID=100
