FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLCSIM_DATA_DIR=/var/lib/plc-sim \
    PLCSIM_CSV=/opt/plc-sim/data/szlab_plc_0810.csv

WORKDIR /opt/plc-sim

COPY PLC-Sim/ /opt/plc-sim/
RUN python -m pip install --no-cache-dir . \
    && groupadd --gid 10001 plc-sim \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/plc-sim plc-sim \
    && mkdir -p /var/lib/plc-sim \
    && chown -R 10001:10001 /var/lib/plc-sim

USER 10001:10001

EXPOSE 18765 4855

ENTRYPOINT ["plc-sim"]
CMD ["gui", "--host", "0.0.0.0", "--port", "18765", "--no-open"]

