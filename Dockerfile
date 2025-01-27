FROM binhex/arch-base:latest AS base
FROM base AS build

WORKDIR ${HOME}
COPY bin bin
COPY src/cbtools src/cbtools
COPY setup.py .

RUN pacman -Syy --noconfirm --noprogressbar python-pip
RUN python -m pip install --user --no-input --break-system-packages --no-warn-script-location --root-user-action ignore .

FROM base

RUN pacman -Syy --noconfirm --noprogressbar waifu2x-ncnn-vulkan
RUN pacman -Scc --noconfirm

COPY --from=build ${HOME}/.local ${HOME}/.local
ENV PATH=${HOME}/.local/bin:${PATH}
