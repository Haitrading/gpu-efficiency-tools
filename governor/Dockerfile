FROM python:3.12-alpine
WORKDIR /opt/governor
COPY request_governor.py ./request_governor.py
RUN mkdir -p /data && chown 65534:65534 /data
USER 65534:65534
ENV PYTHONUNBUFFERED=1 GEMMA_GOVERNOR_HOME=/data
VOLUME /data
ENTRYPOINT ["python3", "-u", "/opt/governor/request_governor.py"]
CMD ["serve"]
