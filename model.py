"""spacewiki database models"""
import logging
import peewee
import playhouse.migrate
import settings
import re
import datetime

database = peewee.SqliteDatabase(settings.DATABASE, threadlocals=True)

class BaseModel(peewee.Model):
    class Meta:
        database = database

class SlugField(peewee.CharField):
    def coerce(self, value):
        return self.slugify(value)

    @staticmethod
    def slugify(title):
        """Translates a string into a reduced character set"""
        return re.sub(r'[^\w]', '_', title.lower())

class Page(BaseModel):
    title = peewee.CharField(unique=True)
    slug = SlugField(unique=True)

    def newRevision(self, body, message):
        """Creates a new Revision of this Page with the given body"""
        return Revision.create(page=self, body=body, message=message)

    def makeSoftlinkFrom(self, prev):
        logging.debug("Linking from %s to %s", prev.slug, self.slug)
        try:
            Softlink.get(Softlink.src == prev, Softlink.dest == self)
            logging.debug("Link exists!")
        except peewee.DoesNotExist:
            Softlink.create(src=prev, dest=self)
            logging.debug("New link!")
        Softlink.update(hits = Softlink.hits + 1).where(Softlink.src ==
            prev, Softlink.dest == self).execute()

    @classmethod
    def latestRevision(cls, slug):
        try:
            return Revision.select() \
                .join(cls) \
                .where(cls.slug == slug) \
                .order_by(Revision.id.desc())[0]
        except IndexError:
            return None

class Softlink(BaseModel):
    src = peewee.ForeignKeyField(Page, related_name='softlinks_out')
    dest = peewee.ForeignKeyField(Page, related_name='softlinks_in')
    hits = peewee.IntegerField(default=0)

class Revision(BaseModel):
    page = peewee.ForeignKeyField(Page, related_name='revisions')
    body = peewee.TextField()
    message = peewee.TextField(default='')
    timestamp = peewee.DateTimeField(default=datetime.datetime.now)

    @property
    def is_latest(self):
      return Page.latestRevision(self.page.slug) == self

    @property
    def prev(self):
      try:
        return Revision.select().where(Revision.page == self.page, Revision.id <
            self.id).order_by(Revision.id.desc()).limit(1)[0]
      except IndexError:
        return None

    @property
    def next(self):
      try:
        return Revision.select().where(Revision.page == self.page, Revision.id >
            self.id).order_by(Revision.id).limit(1)[0]
      except IndexError:
        return None

class Attachment(BaseModel):
    page = peewee.ForeignKeyField(Page, related_name='attachments')
    filename = peewee.CharField(unique=True)
    slug = SlugField(unique=True)

    class Meta:
        indexes = (
            (('page', 'slug'), True),
        )

class AttachmentRevision(BaseModel):
    attachment = peewee.ForeignKeyField(Attachment, related_name='revisions')
    sha = peewee.CharField()

    class Meta:
        indexes = (
            (('attachment', 'sha'), True),
        )

class DatabaseVersion(BaseModel):
    schema_version = peewee.IntegerField(default=0)

def syncdb(app):
    logging.info("Creating tables...")
    try:
        DatabaseVersion.select().execute()
    except peewee.OperationalError:
        database.create_tables([DatabaseVersion])
    try:
        v = DatabaseVersion.select()[0]
    except IndexError:
        v = DatabaseVersion.create(schema_version=0)
    v.schema_version = migrate(v.schema_version)
    v.save()
    logging.info("OK!")

def migrate(currentRevision):
    migrator = playhouse.migrate.SqliteMigrator(database)
    with database.transaction():
        if currentRevision == 0:
            try:
                database.create_tables([Page, Revision, Softlink])
            except peewee.OperationalError:
                pass
            return migrate(1)
        if currentRevision == 1:
            playhouse.migrate.migrate(
                migrator.add_column('revision', 'message', Revision.message),
                migrator.add_column('revision', 'timestamp', Revision.timestamp)
            )
            return migrate(2)
        if currentRevision == 2:
            try:
                database.create_tables([Attachment, AttachmentRevision])
            except peewee.OperationalError:
                pass
            return migrate(3)
    return currentRevision
