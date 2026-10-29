FROM python:3.12-alpine
WORKDIR /opt/monitor
COPY amdgpu_smi.py ./amdgpu_smi.py
USER 65534:65534
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-u", "/opt/monitor/amdgpu_smi.py"]
