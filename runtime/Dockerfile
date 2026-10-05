FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
LABEL org.distributedexec.runtime="stdlib-v1"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY runner.py /runtime/runner.py
RUN chmod 555 /runtime/runner.py && mkdir /input /output && chmod 777 /output
USER 65532:65532
ENTRYPOINT ["python", "-I", "/runtime/runner.py"]
