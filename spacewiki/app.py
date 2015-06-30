"""SpaceWiki Flask application"""

from beaker.middleware import SessionMiddleware
from flask import Flask
import logging
import tempfile

from spacewiki import context, history, model, pages, specials, uploads

APP = Flask(__name__,
            template_folder='../templates',
            static_folder='../static')

APP.config.from_object('spacewiki.settings')
APP.register_blueprint(context.BLUEPRINT)
APP.register_blueprint(model.BLUEPRINT)
APP.register_blueprint(uploads.BLUEPRINT)
APP.register_blueprint(pages.BLUEPRINT)
APP.register_blueprint(history.BLUEPRINT)
APP.register_blueprint(specials.BLUEPRINT)

if APP.config['TEMP_DIR'] is None:
    APP.config['TEMP_DIR'] = tempfile.mkdtemp(prefix='spacewiki')

APP.wsgi_app = SessionMiddleware(APP.wsgi_app, {
    'session.type': 'file',
    'session.cookie_expires': False,
    'session.data_dir': APP.config['TEMP_DIR']
})

if APP.config['ADMIN_EMAILS']:
    from logging.handlers import SMTPHandler

    MAIL_HANDLER = SMTPHandler('127.0.0.1',
                               'spacewiki@localhost',
                               APP.config['ADMIN_EMAILS'],
                               'SpaceWiki error')
    MAIL_HANDLER.setLevel(logging.ERROR)
    APP.logger.addHandler(MAIL_HANDLER)
