FROM tailucas/base-app:20230126_2
# for system/site packages
USER root
# user scripts
COPY backup_auth_token.sh .
# cron jobs
RUN rm -f ./config/cron/base_job
COPY config/cron/backup_auth_token ./config/cron/
COPY config/cron/cleanup_snapshots ./config/cron/
# override dependencies
COPY requirements.txt .
# apply override
RUN /opt/app/app_setup.sh
# switch to user
USER app
COPY config ./config
COPY settings.yaml .
# remove base_app
RUN rm -f /opt/app/base_app
# add the project application
COPY snapshot_processor .
# override entrypoint
COPY app_entrypoint.sh .
CMD ["/opt/app/entrypoint.sh"]
