FROM tailucas/base-app:latest
# for system/site packages
USER root
# generate correct locales
ARG LANG
ARG LANGUAGE
RUN locale-gen ${LANGUAGE} \
    && locale-gen ${LANG} \
    && update-locale \
    && locale -a
# user scripts
COPY backup_auth_token.sh .
# cron jobs
RUN rm -f ./config/cron/base_job
COPY config/cron/backup_auth_token ./config/cron/
COPY config/cron/cleanup_snapshots ./config/cron/
# apply override
RUN "${APP_DIR}/app_setup.sh"
COPY config ./config
COPY settings.yaml .
COPY uv.lock pyproject.toml .python-version ./
RUN chown app:app uv.lock
# switch to user
USER app
RUN "${APP_DIR}/python_setup.sh"
# https://docs.ultralytics.com/quickstart/#custom-installation-methods
# https://docs.astral.sh/uv/guides/integration/pytorch/#configuring-accelerators-with-optional-dependencies
RUN uv pip install ultralytics --no-deps && \
  uv pip install opencv-python-headless && \
  uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
  uv pip install numpy matplotlib polars pyyaml pillow psutil requests scipy ultralytics-thop
# add the project application
COPY app/__main__.py ./app/
COPY app/ftp_server.py ./app/
# override entrypoint
COPY app_entrypoint.sh .
CMD ["/opt/app/entrypoint.sh"]
