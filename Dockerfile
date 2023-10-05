FROM tailucas/base-app:20231004
# for system/site packages
USER root
# generate correct locales
ARG LANG
ENV LANG=$LANG
ARG LANGUAGE
ENV LANGUAGE=$LANGUAGE
ARG LC_ALL
ENV LC_ALL=$LC_ALL
ARG ENCODING
ENV ENCODING=$ENCODING
RUN sed -i -e "s/# ${LANG} ${ENCODING}/${LANG} ${ENCODING}/" /etc/locale.gen && \
    dpkg-reconfigure --frontend=noninteractive locales && \
    update-locale LANG=${LANG} && locale
# user scripts
COPY backup_auth_token.sh .
# cron jobs
RUN rm -f ./config/cron/base_job
COPY config/cron/backup_auth_token ./config/cron/
COPY config/cron/cleanup_snapshots ./config/cron/
# apply override
RUN /opt/app/app_setup.sh
# switch to user
USER app
COPY config ./config
COPY settings.yaml .
COPY poetry.lock pyproject.toml ./
RUN /opt/app/python_setup.sh
# add the project application
COPY app/__main__.py ./app/
COPY app/ftp_server.py ./app/
# override entrypoint
COPY app_entrypoint.sh .
CMD ["/opt/app/entrypoint.sh"]
