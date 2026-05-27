FROM binhex/arch-base:latest

RUN pacman -S --needed --noconfirm python-pip 7zip waifu2x-ncnn-vulkan glslang git

WORKDIR /app
COPY bin bin
COPY src/cbtools src/cbtools
COPY setup.py setup.py
COPY LICENSE LICENSE

RUN pip install '.[spreads]' --no-input --break-system-packages

CMD ["cbmanager"]
