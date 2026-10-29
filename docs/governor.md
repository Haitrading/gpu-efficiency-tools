# Experimental software request pacing

`governor/request_governor.py` is a local OpenAI-compatible HTTP proxy. It intentionally inserts cooldowns *between* requests; it cannot cap instantaneous GPU watts, and it does not change GPU clocks or fan behavior.

Run on a trusted machine:

```bash
python3 governor/request_governor.py init
python3 governor/request_governor.py set --factor 0.6
python3 governor/request_governor.py serve
```

Defaults: 127.0.0.1 port 18084, upstream 127.0.0.1 port 18083; override config in `~/.local/state/gemma-governor`. Only traffic routed through the proxy is paced. Avoid exposing the listener publicly. No authentication/rate limiting. No production security review of all streaming clients has been completed.

Docker demonstration (Linux, localhost upstream):

```bash
docker build -t llm-request-governor:local ./governor
docker volume create llm-governor-state
docker run --rm --network host --read-only --cap-drop ALL --security-opt no-new-privileges \
  -v llm-governor-state:/data llm-request-governor:local init
docker run --rm -it --network host --read-only --cap-drop ALL --security-opt no-new-privileges \
  -v llm-governor-state:/data llm-request-governor:local serve
```

An added cooldown can lower time-averaged GPU power while increasing task duration. Measure energy per completed task; **no efficiency improvement is guaranteed**.
