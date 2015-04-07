"""Spacewiki settings"""
DATABASE = 'spacewiki.sqlite3'
SITE_NAME = 'SpaceWiki'
INDEX_PAGE = 'index'
UPLOAD_PATH = 'uploads'

ADMIN_EMAILS = None

try:
    from local_settings import * #pylint: disable=unused-wildcard-import,wildcard-import
except ImportError:
    pass
