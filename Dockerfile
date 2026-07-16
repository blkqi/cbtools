FROM binhex/arch-base:latest

RUN pacman -S --needed --noconfirm python-pip 7zip waifu2x-ncnn-vulkan glslang

WORKDIR /app
COPY bin bin
COPY src/cbtools src/cbtools
COPY setup.py setup.py
COPY LICENSE LICENSE
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN pip install /app --no-input --break-system-packages \
 && chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["cbmanager"]
