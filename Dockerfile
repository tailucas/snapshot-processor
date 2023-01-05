FROM tailucas/base-app:20230105
# for system/site packages
USER root
# override dependencies
COPY requirements.txt .
# apply override
RUN /opt/app/app_setup.sh
# cron jobs
ADD config/backup_auth_token /etc/cron.d/backup_auth_token
RUN crontab -u app /etc/cron.d/backup_auth_token
RUN chmod 0600 /etc/cron.d/backup_auth_token
ADD config/cleanup_snapshots /etc/cron.d/cleanup_snapshots
RUN crontab -u app /etc/cron.d/cleanup_snapshots
RUN chmod 0600 /etc/cron.d/cleanup_snapshots
# switch to user
USER app
COPY config ./config
COPY settings.yaml .
COPY backup_auth_token.sh .
# remove base_app
RUN rm -f /opt/app/base_app
# add the project application
COPY snapshot_processor .
COPY ftp_server .
# override entrypoint
COPY app_entrypoint.sh .
CMD ["/opt/app/entrypoint.sh"]
