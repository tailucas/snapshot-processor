FROM tailucas/base-app:latest
# production image: uv installs main dependencies only (ignore default dependency groups)
ENV UV_NO_DEFAULT_GROUPS=1
# for system/site packages
USER root
# generate correct locales
ARG LANG
ARG LANGUAGE
RUN locale-gen ${LANGUAGE} \
    && locale-gen ${LANG} \
    && update-locale \
    && locale -a
# start-up and maintenance scripts (rarely change)
COPY backup_auth_token.sh app_entrypoint.sh ./
# cron jobs
COPY config/cron/backup_auth_token ./config/cron/
COPY config/cron/cleanup_snapshots ./config/cron/
# remove base cron job and register ours
RUN rm -f ./config/cron/base_job \
    && "${APP_DIR}/app_setup.sh"
# dependency files (changes trigger expensive re-install)
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
# create config directories for ML libraries
RUN mkdir -p /home/app/.config/Ultralytics /home/app/.config/matplotlib \
    && chown -R app:app /home/app/.config/
# switch to user
USER app
# install main Python dependencies
RUN "${APP_DIR}/python_setup.sh"
# install object detection stack
# https://docs.ultralytics.com/quickstart/#custom-installation-methods
# https://docs.astral.sh/uv/guides/integration/pytorch/#configuring-accelerators-with-optional-dependencies
RUN uv pip install ultralytics --no-deps && \
  uv pip install opencv-python-headless && \
  uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
  uv pip install numpy matplotlib polars pyyaml pillow psutil requests scipy ultralytics-thop
# configuration (frequent changes, but only invalidates the cheap COPY layers below)
COPY settings.yaml .
COPY config ./config
# add the project application
COPY --chown=app:app app/ ./app/
CMD ["/opt/app/entrypoint.sh"]
