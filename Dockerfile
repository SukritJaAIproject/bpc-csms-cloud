# BPC EV Charging Platform - CSMS (operator console + BPC EV app + pay page + OCPP 1.6-J) in one container.
# Built and run by Render (free web service) or any Docker host:  docker build -t bpc-csms . && docker run -p 10000:10000 ...
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Thimphu

RUN apt-get update \
 && apt-get install -y --no-install-recommends tini tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY csms_web_ui_flexxfast1.py mock_cp.py start.sh ./
RUN sed -i 's/\r$//' start.sh \
 && chmod +x start.sh \
 && useradd --create-home --uid 10001 csms \
 && chown -R csms:csms /app
USER csms

# PORT: the one public port (Render sets it, default 10000): web console, app, pay page and OCPP (/ocpp/<id>).
# CSMS_OCPP_PORT=9000 would add the classic separate OCPP port (bound to CSMS_OCPP_BIND).
ENV PORT=10000 \
    CSMS_USER=admin \
    CSMS_TRUST_PROXY=1 \
    CSMS_OCPP_PORT=0 \
    CSMS_OCPP_BIND=127.0.0.1 \
    CSMS_SIMULATORS="BPC-THIMPHU BPC-PARO BPC-PUNAKHA"
EXPOSE 10000
ENTRYPOINT ["tini", "--"]
CMD ["./start.sh"]
