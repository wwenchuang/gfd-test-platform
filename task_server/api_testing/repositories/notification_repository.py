"""Transaction-scoped notification channel persistence."""

from sqlalchemy import select

from ..models.notification import ApiNotificationChannel
from .source_repository import audit_fields


class NotificationRepository:
    def __init__(self, session):
        self.session = session

    def get(self, owner_id, project_id, channel_type, *, for_update=False):
        statement = select(ApiNotificationChannel).where(
            ApiNotificationChannel.owner_id == owner_id,
            ApiNotificationChannel.project_id == project_id,
            ApiNotificationChannel.channel_type == channel_type,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def create(self, owner_id, project_id, channel_type, name, actor_id):
        record = ApiNotificationChannel(
            project_id=project_id,
            channel_type=channel_type,
            name=name,
            **audit_fields(actor_id),
        )
        record.owner_id = owner_id
        self.session.add(record)
        self.session.flush()
        return record

    def flush(self):
        self.session.flush()
