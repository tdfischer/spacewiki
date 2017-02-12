"""spacewiki database models"""
import crypt
import difflib
import datetime
from flask import g, current_app, Blueprint, url_for
from flask_login import current_user, login_user, UserMixin, AnonymousUserMixin
from flask_script import Manager
from flask.json import JSONEncoder
import os
import peewee
import playhouse.migrate
from playhouse.db_url import connect
import shutil
import slugify
import traceback
import urlparse
import urllib
import hashlib

import spacewiki

BLUEPRINT = Blueprint('model', __name__)

class ModelEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, BaseModel):
            return obj.to_json()
        return JSONEncoder.default(self, obj)

@BLUEPRINT.before_app_request
def get_db():
    """Sets up the database"""
    db = getattr(g, '_database', None)
    if db is None:
        current_app.logger.info("Using database at %s", current_app.config['DATABASE_URL'])
        g._database = db = connect(current_app.config['DATABASE_URL'])
    DATABASE.initialize(db)

DATABASE = peewee.Proxy()


class BaseModel(peewee.Model):
    """Base class for SpaceWiki models, so all models share the same database"""
    class Meta:  # pylint: disable=missing-docstring,no-init,old-style-class,too-few-public-methods
        database = DATABASE


class SlugField(peewee.CharField):
    """Normalizes strings into a url-friendly 'slug'"""

    @staticmethod
    def slugify(title):
        """Translates a string into a reduced character set"""
        parts = unicode(title).split('/')
        return '/'.join(map(SlugField._slugify, parts)).rstrip('/')

    @staticmethod
    def _slugify(value):
        # For sql queries
        if value == '%':
            return value
        return slugify.slugify(value)

    @classmethod
    def split_title(cls, title):
        """Splits apart the parent slug from the title, eg some/parent/Title"""
        parts = unicode(title).split('/')
        slug = cls.slugify('/'.join(parts[0:-1]))
        title = parts[-1]
        return (slug, title)

    @classmethod
    def mangle_full_slug(cls, slug, title):
        subslug, title = cls.split_title(title)
        if slug == '':
          return (subslug, title)
        return ('/'.join((slug, subslug)), title)

class Page(BaseModel):
    """A wiki page"""
    title = peewee.CharField(unique=False)
    slug = SlugField(unique=True)

    def to_json(self):
        def map_nav(p):
            return {'title': p.title, 'slug': '/'+p.slug}
        def map_links(link):
            return {'title': link.dest.title, 'slug': '/'+link.dest.slug}
        def map_links_in(link):
            return {'title': link.src.title, 'slug': '/'+link.src.slug}
        all_links = map(map_links, list(self.softlinks_out))
        all_links += map(map_links_in, list(self.softlinks_in))
        unique_links = []
        for x in all_links:
            if x not in unique_links:
                unique_links.append(x)
        return {
            'title': self.title,
            'slug': self.slug,
            'revisions': list(self.revisions),
            'latest': self.latestRevision(self.slug),
            'attachments': list(self.attachments),
            'softlinks': unique_links,
            'navigation': {
                'siblings': map(map_nav, list(self.siblings)),
                'subpages': map(map_nav, list(self.subpages)),
                'parents': map(map_nav, list(self.parentPages))
            }
        }

    @staticmethod
    def parsePreviousSlugFromRequest(req, default):
        referer = req.headers.get('x-spacewiki-referer', req.headers.get('referer', None))
        if referer is not None:
            refer_url = urlparse.urlparse(referer)
            if 'Host' in req.headers:
                if refer_url.netloc == req.headers['Host']:
                    script_name = req.environ['SCRIPT_NAME']

                    last_page_slug = urllib.unquote(
                        refer_url.path.replace(script_name, '', 1)
                    )
                    current_app.logger.debug("script_name: %s referrer: %s", script_name, last_page_slug)
                    last_page_slug = last_page_slug.strip('/')
                    if last_page_slug == "":
                        last_page_slug = default
                    req.lastSlug = last_page_slug
                    return last_page_slug
        if 'lastSlug' in req.args:
            req.lastSlug = req.args.get('lastSlug')
            return req.lastSlug
        req.lastSlug = None
        return None

    def newRevision(self, body, message, author):
        """Creates a new Revision of this Page with the given body"""
        current_app.logger.debug("Creating new revision on %s", self.slug)
        return Revision.create(page=self, body=body, message=message,
                               author=author)

    def makeSoftlinkFrom(self, prev):
        current_app.logger.debug("Linking from %s to %s", prev.slug, self.slug)

        if prev == self:
            current_app.logger.debug("Refusing to link %s to itself", prev.slug)
            return

        try:
            Softlink.get(Softlink.src == prev, Softlink.dest == self)
            current_app.logger.debug("Link exists!")
        except peewee.DoesNotExist:
            Softlink.create(src=prev, dest=self)
            current_app.logger.debug("New link!")

        Softlink.update(hits=Softlink.hits + 1) \
                .where(Softlink.src == prev, Softlink.dest == self) \
                .execute()

    def attachUpload(self, src, filename, uploadPath):
        assert isinstance(src, basestring)
        assert isinstance(filename, basestring)
        assert isinstance(uploadPath, basestring)

        current_app.logger.info("Attaching upload %s (%s), saved at %s",
                     src, filename, uploadPath)

        hex_sha = Attachment.hashFile(src)
        saved_name = os.path.join(uploadPath,
                                 Attachment.hashPath(hex_sha, filename))

        if not os.path.exists(os.path.dirname(saved_name)):
            os.makedirs(os.path.dirname(saved_name))
        shutil.move(src, saved_name)

        # FIXME: These db queries should be handled by the model
        try:
            attachment = Attachment.get(page=self, slug=filename)
            current_app.logger.debug("Updating existing attachment: %s", attachment.slug)
        except peewee.DoesNotExist:
            attachment = Attachment.create(page=self, filename=filename,
                                           slug=filename)
            current_app.logger.debug("Creating new attachment: %s", attachment.slug)

        try:
            AttachmentRevision.get(attachment=attachment, sha=hex_sha)
            current_app.logger.debug("Duplicate file upload: %s", attachment.slug)
        except peewee.DoesNotExist:
            AttachmentRevision.create(attachment=attachment, sha=hex_sha)
            current_app.logger.debug("New upload: %s -> %s", attachment.slug, hex_sha)

        current_app.logger.info("Uploaded file %s to %s", filename, saved_name)

    @classmethod
    def latestRevision(cls, slug):
        try:
            return Revision.select() \
                .join(cls) \
                .where(cls.slug == slug) \
                .order_by(Revision.id.desc())[0]  # pylint: disable=no-member
        except IndexError:
            return None

    @property
    def subpages(self):
        return Page.select().where(peewee.fn.Substr(Page.slug, 1,
            len(self.slug)+1) == self.slug+'/').order_by(Page.title)

    @property
    def parentPages(self):
        if '/' not in self.slug:
            return []
        parentSlug = '/'.join(self.slug.split('/')[0:-1])
        if parentSlug == "":
            parentSlug = current_app.config['INDEX_PAGE']
        try:
            parent = Page.select().where(Page.slug == parentSlug)[0]
            return parent.parentPages + [parent,]
        except IndexError:
            return []

    @property
    def siblings(self):
        parentSlug = '/'.join(self.slug.split('/')[0:-1])
        if parentSlug == "":
            return Page.select().where(~Page.slug.contains('/')).order_by(Page.title)
        try:
            parent = Page.get(Page.slug == parentSlug)
        except peewee.DoesNotExist:
            return []
        return parent.subpages

    @property
    def parent_tree(self):
        ret = []
        buf = []
        for r in self.slug.split('/')[0:-1]:
          buf.append(r)
          ret.append({'title': r, 'slug': '/'.join(buf)})
        return ret

class Softlink(BaseModel):
    """An organic automatically generated link between pages"""
    src = peewee.ForeignKeyField(Page, related_name='softlinks_out')
    dest = peewee.ForeignKeyField(Page, related_name='softlinks_in')
    hits = peewee.IntegerField(default=0)

class Identity(BaseModel, UserMixin):
    """An identity in the wiki"""
    display_name = peewee.CharField()
    handle = peewee.CharField()
    auth_id = peewee.CharField()
    auth_type = peewee.CharField()

    def to_json(self):
        return {
            'display': self.display_name,
            'handle': self.handle,
            'id': self.get_id()
        }

    def __repr__(self):
        return "Identity(%s, %s)"%(self.id, self.get_id())

    @property
    def is_anonymous(self):
        return self.auth_type == 'tripcode'

    @property
    def is_authenticated(self):
        return self.auth_type != 'tripcode'

    def get_id(self):
        return self.auth_type + ':' + self.auth_id

    @staticmethod
    def get_from_id(user_id, display=None, handle=None):
        provider, id = user_id.split(':', 1)
        try:
            ret = Identity.get(auth_type=provider, auth_id=id)
            return ret
        except:
            ret = Identity(auth_type=provider, auth_id=id)
        if display is not None:
            ret.display_name = display
        if handle is not None:
            ret.handle = handle
        return ret

    @staticmethod
    def get_or_create_from_id(*args, **kwargs):
        ret = Identity.get_from_id(*args, **kwargs)
        ret.save()
        return ret

class Revision(BaseModel):
    """A page revision"""
    page = peewee.ForeignKeyField(Page, related_name='revisions')
    body = peewee.TextField()
    message = peewee.TextField(default='')
    timestamp = peewee.DateTimeField(default=datetime.datetime.now)
    author = peewee.ForeignKeyField(Identity, related_name='revisions')

    def to_json(self):
        return {
            'body': self.body,
            'id': self.id,
            'timestamp': self.timestamp,
            'author': self.author,
            'message': self.message,
            'rendered': self.html
        }

    @property
    def summary(self):
        return self.body[0:500]

    @staticmethod
    def render_text(body, slug):
        """Renders a string of wiki text as HTML"""
        try:
            return spacewiki.wikiformat.render_wikitext(body, slug)
        except Exception:  # pylint: disable=broad-except
            return "Error in processing wikitext:" + \
                "<pre>" + \
                traceback.format_exc() + \
                "</pre>"

    @property
    def html(self):
        """Renders this revision's body (which is wikitext) as HTML"""
        return self.render_text(self.body,
                                self.page.slug)  # pylint: disable=no-member

    @property
    def is_latest(self):
        """Returns True if this is the latest revision of a page, false
        otherwise"""
        return Page.latestRevision(self.page.slug) == self  # pylint: disable=no-member

    @property
    def prev(self):
        """Returns the previous revision if there is one, None otherwise"""
        try:
            return Revision.select() \
                           .where(Revision.page == self.page,
                                  Revision.id < self.id) \
                           .order_by(Revision.id.desc()) \
                           .limit(1)[0]
        except IndexError:
            return None

    @classmethod
    def _makeDiff(cls, r1, r2):
        if r1 is None:
            diff = difflib.unified_diff("",
                                        r2.body.split("\n"),
                                        lineterm="",
                                        fromfile="%s@%s" % (r2.page.slug, 0),
                                        tofile="%s@%s" % (r2.page.slug, r2.id))
        elif r2 is None:
            diff = difflib.unified_diff(r1.body.split("\n"),
                                        "",
                                        lineterm="",
                                        fromfile="%s@%s" % (
                                            r1.page.slug,
                                            r1.id),
                                        tofile="%s@%s" % (r1.page.slug, 0))
        else:
            diff = difflib.unified_diff(r1.body.split("\n"),
                                        r2.body.split("\n"),
                                        lineterm="",
                                        fromfile="%s@%s" % (
                                            r1.page.slug,
                                            r1.id),
                                        tofile="%s@%s" % (r2.page.slug, r2.id))
        return cls._parseDiff(diff)

    @staticmethod
    def _parseDiff(diff):
        ret = []
        for line in diff:
            if line.startswith('+++') or line.startswith('---'):
                line_type = 'meta'
            elif line.startswith('@@'):
                line_type = 'context'
            elif line.startswith('+'):
                line_type = 'addition'
            elif line.startswith('-'):
                line_type = 'subtraction'
            ret.append({'contents': line, 'type': line_type})
        return ret

    def diffTo(self, prev):
        return self._makeDiff(self, prev)

    @property
    def diffToPrev(self):
        prev_rev = self.prev

        if prev_rev is not None:
            return self.diffTo(prev_rev)

        return self._makeDiff(None, self)

    @property
    def diffToNext(self):
        next_rev = self.next

        if next_rev is not None:
            return self.diffTo(next_rev)

        return self._makeDiff(self, None)

    def diffStatsToPrev(self):
        meta = {'additions': 0, 'subtractions': 0}
        for line in self.diffToPrev:
            if line['type'] == 'addition':
                meta['additions'] += 1
            if line['type'] == 'subtraction':
                meta['subtractions'] += 1
        return meta

    @property
    def next(self):
        """Returns the next revision if one exists, None otherwise"""
        try:
            return Revision.select() \
                           .where(Revision.page == self.page,
                                  Revision.id > self.id) \
                           .order_by(Revision.id) \
                           .limit(1)[0]
        except IndexError:
            return None


class Attachment(BaseModel):
    """A file attached to a page"""
    page = peewee.ForeignKeyField(Page, related_name='attachments')
    filename = peewee.CharField(unique=True)
    slug = SlugField(unique=True)

    def to_json(self):
        return {
            'filename': self.filename,
            'slug': self.slug,
            'url': url_for('uploads.get_attachment', slug=self.page.slug,
                fileslug=self.slug)
        }

    @staticmethod
    def hashFile(src):
        with open(src, 'r') as f:
            sha = hashlib.sha256()
            sha.update(f.read())
        return sha.hexdigest()

    @staticmethod
    def hashPath(sha, src):
        return "%s/%s/%s-%s" % (sha[0:2], sha[2:4], sha, src)

    @classmethod
    def findAttachment(cls, pageSlug, fileSlug):
        try:
            Page.get(slug=pageSlug)
        except peewee.DoesNotExist:
            return None
        try:
            attachment = Attachment.get(slug=fileSlug)
        except peewee.DoesNotExist:
            return None
        return attachment

    class Meta:  # pylint: disable=missing-docstring,no-init,old-style-class,too-few-public-methods
        indexes = (
            (('slug','page'), True),
        )


class AttachmentRevision(BaseModel):
    """A revision of an uploaded file"""
    attachment = peewee.ForeignKeyField(Attachment, related_name='revisions')
    sha = peewee.CharField()

    class Meta:  # pylint: disable=missing-docstring,no-init,old-style-class,too-few-public-methods
        indexes = (
            (('attachment', 'sha'), True),
        )
        order_by = ('-id',)


class DatabaseVersion(BaseModel):
    """That all-too-familiar hack to encode the schema version in the
    database"""
    schema_version = peewee.IntegerField(default=0)

MANAGER = Manager(usage='Database tools')


@MANAGER.command
def syncdb():
    """Creates and updates database schema"""
    with current_app.app_context():
        current_app.logger.info("Creating tables...")
        get_db()
        current_app.logger.info("Creating tables")
        DATABASE.create_tables([Page, Revision, Softlink, Attachment,
            AttachmentRevision, DatabaseVersion, Identity], True)

        start_version = 0
        initial_schema = False
        try:
            version = DatabaseVersion.select()[0]
            start_version = version.schema_version
        except IndexError:
            current_app.logger.debug("Creating initial schema")
            version = DatabaseVersion.create(schema_version=0)
            initial_schema = True

        if initial_schema:
            version.schema_version = len(MIGRATIONS)
        else:
            current_app.logger.debug("Database schema is at version %s",
                    version.schema_version)
            try:
                while version.schema_version < len(MIGRATIONS):
                        run_migrations(version.schema_version)
                        version.schema_version += 1
            except:
                current_app.logger.exception("Could not update database schema to version %s! Fix any errors and re-run syncdb again.", version.schema_version + 1)
        if version.schema_version != start_version:
            current_app.logger.debug("Database schema is now at version %s",
                    version.schema_version)
        version.save()
        if version.schema_version == len(MIGRATIONS):
            current_app.logger.info("OK!")

def migrate_identities(migrator):
    playhouse.migrate.migrate(
        migrator.rename_column('revision', 'author', 'tripcode')
    )

    author_id_field = peewee.IntegerField(default=0)
    playhouse.migrate.migrate(
        migrator.add_column('revision', 'author_id', author_id_field)
    )

    current_app.logger.info("Converting tripcodes to identities")
    for r in Revision.raw('SELECT id,tripcode FROM revision'):
        id = Identity.from_tripcode(r.tripcode)
        r.author = id
        r.save()

    playhouse.migrate.migrate(
        migrator.drop_column('revision', 'tripcode')
    )

MIGRATIONS = (
    migrate_identities,
)

def run_migrations(current_revision):
    """Runs migrations starting at current_revision"""
    migrator = playhouse.migrate.SqliteMigrator(DATABASE)

    for migration in MIGRATIONS[current_revision:]:
        with DATABASE.transaction():
            current_app.logger.info("Applying migration %d -> %d", current_revision,
                    current_revision+1)
            migration(migrator)

    current_app.logger.info("Upgraded to schema %s", current_revision)
