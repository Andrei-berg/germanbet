import os
from datetime import timezone, timedelta

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_bootstrap import Bootstrap5
from flask_wtf import CSRFProtect
from app.config import Config

db = SQLAlchemy()
bootstrap = Bootstrap5()
csrf = CSRFProtect()

MSK = timezone(timedelta(hours=3))


def create_app(config_class=Config):
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates'),
                static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static'))
    app.config.from_object(config_class)

    db.init_app(app)
    bootstrap.init_app(app)
    csrf.init_app(app)

    @app.template_filter("msk")
    def _msk(dt):
        if dt is None:
            return dt
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MSK)

    @app.template_filter("dtfmt")
    def _dtfmt(dt, fmt="%d.%m %H:%M"):
        if dt is None:
            return ""
        return dt.strftime(fmt)

    with app.app_context():
        from app.routes import main
        app.register_blueprint(main.bp)
        db.create_all()

    return app
