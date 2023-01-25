FROM tailucas/base-app:20230125
# for system/site packages
USER root
# system setup
RUN apk update \
    && apk upgrade \
    && apk --no-cache add \
        jq
# override dependencies
ENV PYTHON_ADD_WHEEL 1
COPY requirements.txt .
# apply override
RUN /opt/app/app_setup.sh
# cron jobs
COPY config/backup_auth_token ./config/
RUN crontab -u app /opt/app/config/backup_auth_token
COPY config/cleanup_snapshots ./config/
RUN crontab -u app /opt/app/config/cleanup_snapshots
# switch to user
USER app
COPY config ./config
COPY settings.yaml .
COPY backup_auth_token.sh .
# remove base_app
RUN rm -f /opt/app/base_app
# add the project application
COPY snapshot_processor .
# override entrypoint
COPY app_entrypoint.sh .
CMD ["/opt/app/entrypoint.sh"]
